// OPT6: compact host storage for the original iSLIP grant/accept policy.
#ifndef BOOKSIM_ISLIP_COMPACT_HPP
#define BOOKSIM_ISLIP_COMPACT_HPP

#include "allocator.hpp"

#if OPT6
#include <cstddef>
#include <cstdint>

class iSLIP_Compact : public Allocator {
  int _iSLIP_iter;
  vector<sRequest> _requests;
  vector<uint64_t> _rows;
  vector<uint64_t> _columns;
  vector<uint64_t> _grants;
  vector<int> _gptrs;
  vector<int> _aptrs;

  size_t _Index(int in, int out) const;
  static int _FirstFrom(uint64_t mask, int offset);

public:
  iSLIP_Compact(Module *parent, const string& name,
                int inputs, int outputs, int iters);

  void Clear();
  int ReadRequest(int in, int out) const;
  bool ReadRequest(sRequest &req, int in, int out) const;
  void AddRequest(int in, int out, int label = 1,
                  int in_pri = 0, int out_pri = 0);
  void RemoveRequest(int in, int out, int label = 1);
  void ReplaceRequest(int in, int out, int old_label, int label,
                      int in_pri = 0, int out_pri = 0);
  void Allocate();

  bool OutputHasRequests(int out) const;
  bool InputHasRequests(int in) const;
  int NumOutputRequests(int out) const;
  int NumInputRequests(int in) const;
  void PrintRequests(ostream *os = NULL) const;
};
#endif
#endif
