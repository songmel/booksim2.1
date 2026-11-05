// OPT6: avoid per-request tree nodes without changing iSLIP winners or timing.
#include "islip_compact.hpp"

#if OPT6
#include <algorithm>
#include <cassert>
#include <iostream>

iSLIP_Compact::iSLIP_Compact(Module *parent, const string& name,
                             int inputs, int outputs, int iters)
  : Allocator(parent, name, inputs, outputs), _iSLIP_iter(iters)
{
  assert(inputs >= 1 && inputs <= 64);
  assert(outputs >= 1 && outputs <= 64);
  _requests.resize(size_t(inputs) * outputs);
  _rows.resize(inputs, 0);
  _columns.resize(outputs, 0);
  _grants.resize(inputs, 0);
  _gptrs.resize(outputs, 0);
  _aptrs.resize(inputs, 0);
}

size_t iSLIP_Compact::_Index(int in, int out) const
{
  assert(in >= 0 && in < _inputs);
  assert(out >= 0 && out < _outputs);
  return size_t(in) * _outputs + out;
}

int iSLIP_Compact::_FirstFrom(uint64_t mask, int offset)
{
  assert(mask != 0);
  assert(offset >= 0 && offset < 64);
  // OPT6: first bit at/after the RR pointer, wrapping only if necessary.
  uint64_t const after = mask & (~uint64_t(0) << offset);
  return __builtin_ctzll(after ? after : mask);
}

void iSLIP_Compact::Clear()
{
  if(!_dirty) return;
  // OPT6: masks own validity; stale metadata need not be cleared. Keep RR history.
  std::fill(_rows.begin(), _rows.end(), uint64_t(0));
  std::fill(_columns.begin(), _columns.end(), uint64_t(0));
  Allocator::Clear();
}

int iSLIP_Compact::ReadRequest(int in, int out) const
{
  sRequest req;
  return ReadRequest(req, in, out) ? req.label : -1;
}

bool iSLIP_Compact::ReadRequest(sRequest &req, int in, int out) const
{
  size_t const index = _Index(in, out);
  if(!(_rows[in] & (uint64_t(1) << out))) return false;
  req = _requests[index];
  return true;
}

void iSLIP_Compact::AddRequest(int in, int out, int label,
                               int in_pri, int out_pri)
{
  Allocator::AddRequest(in, out, label, in_pri, out_pri);
  size_t const index = _Index(in, out);
  assert(!(_rows[in] & (uint64_t(1) << out)));
  assert(!(_columns[out] & (uint64_t(1) << in)));
  sRequest &req = _requests[index];
  req.port = out;
  req.label = label;
  req.in_pri = in_pri;
  req.out_pri = out_pri;
  _rows[in] |= uint64_t(1) << out;
  _columns[out] |= uint64_t(1) << in;
}

void iSLIP_Compact::RemoveRequest(int in, int out, int label)
{
  size_t const index = _Index(in, out);
  assert(_rows[in] & (uint64_t(1) << out));
  assert(_columns[out] & (uint64_t(1) << in));
  assert(_requests[index].label == label);
  _rows[in] &= ~(uint64_t(1) << out);
  _columns[out] &= ~(uint64_t(1) << in);
}

void iSLIP_Compact::ReplaceRequest(int in, int out, int old_label, int label,
                                   int in_pri, int out_pri)
{
  Allocator::AddRequest(in, out, label, in_pri, out_pri);
  size_t const index = _Index(in, out);
  assert(_rows[in] & (uint64_t(1) << out));
  assert(_columns[out] & (uint64_t(1) << in));
  sRequest &req = _requests[index];
  assert(req.label == old_label);
  req.label = label;
  req.in_pri = in_pri;
  req.out_pri = out_pri;
}

void iSLIP_Compact::Allocate()
{
  for(int iter = 0; iter < _iSLIP_iter; ++iter) {
    // OPT6: reuse grants as an output-bit set per input; finish all grants first.
    std::fill(_grants.begin(), _grants.end(), uint64_t(0));
    uint64_t free_inputs = 0;
    for(int in = 0; in < _inputs; ++in) {
      if(_inmatch[in] == -1) free_inputs |= uint64_t(1) << in;
    }
    for(int out = 0; out < _outputs; ++out) {
      if(_outmatch[out] != -1) continue;
      uint64_t const eligible = _columns[out] & free_inputs;
      if(!eligible) continue;
      int const in = _FirstFrom(eligible, _gptrs[out]);
      _grants[in] |= uint64_t(1) << out;
    }
    for(int in = 0; in < _inputs; ++in) {
      if(!_grants[in]) continue;
      int const out = _FirstFrom(_grants[in], _aptrs[in]);
      _inmatch[in] = out;
      _outmatch[out] = in;
      // OPT6: the original iSLIP advances pointers only on iteration zero.
      if(iter == 0) {
        _gptrs[out] = (in + 1) % _inputs;
        _aptrs[in] = (out + 1) % _outputs;
      }
    }
  }
}

bool iSLIP_Compact::InputHasRequests(int in) const
{
  assert(in >= 0 && in < _inputs);
  return _rows[in] != 0;
}

bool iSLIP_Compact::OutputHasRequests(int out) const
{
  assert(out >= 0 && out < _outputs);
  return _columns[out] != 0;
}

int iSLIP_Compact::NumInputRequests(int in) const
{
  // OPT6: SparseAllocator's occupied-set count is Boolean, not an edge count.
  return InputHasRequests(in) ? 1 : 0;
}

int iSLIP_Compact::NumOutputRequests(int out) const
{
  return OutputHasRequests(out) ? 1 : 0;
}

void iSLIP_Compact::PrintRequests(ostream *os) const
{
  if(!os) os = &std::cout;
  *os << "Input requests = [ ";
  for(int in = 0; in < _inputs; ++in) {
    uint64_t pending = _rows[in];
    if(!pending) continue;
    *os << in << " -> [ ";
    while(pending) {
      int const out = _FirstFrom(pending, 0);
      *os << out << "@" << _requests[_Index(in, out)].in_pri << " ";
      pending &= pending - 1;
    }
    *os << "]  ";
  }
  *os << "], output requests = [ ";
  for(int out = 0; out < _outputs; ++out) {
    uint64_t pending = _columns[out];
    if(!pending) continue;
    *os << out << " -> [ ";
    while(pending) {
      int const in = _FirstFrom(pending, 0);
      *os << in << "@" << _requests[_Index(in, out)].out_pri << " ";
      pending &= pending - 1;
    }
    *os << "]  ";
  }
  *os << "]." << std::endl;
}
#endif
