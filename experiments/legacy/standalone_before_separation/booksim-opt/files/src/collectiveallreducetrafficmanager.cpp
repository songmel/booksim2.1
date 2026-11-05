#include "collectiveallreducetrafficmanager.hpp"

#include <algorithm>
#include <climits>
#include <numeric>

CollectiveAllReduceTrafficManager::CollectiveAllReduceTrafficManager(
    Configuration const & config, std::vector<Network *> const & net)
  : TrafficManager(config, net), _schedule("", 8),
    _bytes(config.GetInt("allreduce_bytes")),
    _max_cycles(config.GetInt("allreduce_max_cycles")), _finished(0),
    _expected_flits(0), _expected_packets(0), _received_flits(0), _received_packets(0)
{
  if(config.GetStr("topology") != "mesh" || config.GetInt("k") != 8 ||
     config.GetInt("n") != 2 || _nodes != 64 || _routers != 64 ||
     config.GetStr("router") != "iq" || config.GetStr("routing_function") != "dor" ||
     config.GetInt("c") != 1 || _classes != 1 || _subnets != 1 ||
     _total_sims != 1 || _use_read_write[0] || config.GetInt("link_failures"))
    Error("Collective experiments require one 8x8 mesh, 64 IQ routers, c=1, DOR, one class/subnet/run and no faults/read-write traffic.");
  if(config.GetInt("flit_bytes") != 16 || config.GetInt("max_packet_flits") != 8 ||
     _vcs != 8 || config.GetInt("vc_buf_size") != 8 ||
     config.GetStr("buffer_policy") != "private")
    Error("Collective experiments require 16-byte flits, 8-flit packets and 8 VCs with 8-flit private buffers.");
  if(_max_cycles <= 0 || _max_cycles >= INT_MAX)
    Error("Invalid AllReduce cycle limit.");
  // This conservative bound covers every schedule before allocating its state.
  if(_bytes <= 0 || uint64_t(_bytes) * 128 / 16 >= INT_MAX)
    Error("AllReduce input exceeds the supported flit-ID range.");
  try {
    std::string algorithm = config.GetStr("allreduce_algorithm");
    if(algorithm == "ring2d") _schedule = allreduce::Ring2D(8, _bytes);
    else if(algorithm == "tto")
      _schedule = allreduce::TTO(8, _bytes, config.GetInt("allreduce_chunk_bytes"));
    else Error("Unknown AllReduce algorithm: " + algorithm);
  } catch(std::exception const & error) { Error(error.what()); }

  _state.resize(_schedule.operations.size());
  _ready.resize(_nodes);
  _last_packet.assign(_nodes, -1);
  _values.assign(_nodes, std::vector<uint64_t>(_schedule.chunk_bytes.size()));
  _expected_sent.assign(_nodes, 0);
  _expected_received.assign(_nodes, 0);
  _expected_links.assign(_nodes * 4, 0);
  for(int node = 0; node < _nodes; ++node)
    std::fill(_values[node].begin(), _values[node].end(),
              _schedule.contributors & (uint64_t(1) << node));
  for(size_t id = 0; id < _state.size(); ++id) {
    allreduce::Operation const & op = _schedule.operations[id];
    _state[id].remaining = op.dependencies;
    if(op.kind == allreduce::BARRIER) continue;
    uint64_t flits = op.bytes / 16;
    _expected_flits += flits;
    _expected_packets += op.bytes / 128;
    _expected_sent[op.source] += flits;
    _expected_received[op.destination] += flits;
    int node = op.source;
    // DOR's physical X-then-Y path, including multi-hop logical ring closure.
    while(node % 8 != op.destination % 8) {
      int port = node % 8 < op.destination % 8 ? 0 : 1;
      _expected_links[4*node + port] += flits;
      node += port == 0 ? 1 : -1;
    }
    while(node / 8 != op.destination / 8) {
      int port = node / 8 < op.destination / 8 ? 2 : 3;
      _expected_links[4*node + port] += flits;
      node += port == 2 ? 8 : -8;
    }
  }
  if(_expected_flits >= INT_MAX ||
     *std::max_element(_expected_sent.begin(), _expected_sent.end()) >= uint64_t(_max_cycles))
    Error("AllReduce exceeds the flit-ID range or serialization cycle budget.");
}

void CollectiveAllReduceTrafficManager::_MakeReady(int id, int cycle) {
  State & state = _state[id];
  if(state.ready >= 0) Error("Collective operation became ready twice.");
  state.ready = cycle;
  if(_schedule.operations[id].kind == allreduce::BARRIER) _Complete(id, cycle);
  else _ready[_schedule.operations[id].source].push_back(id);
}

void CollectiveAllReduceTrafficManager::_Release(std::vector<int> const & successors, int cycle) {
  for(int next : successors) {
    if(--_state[next].remaining < 0) Error("Invalid collective dependency accounting.");
    if(!_state[next].remaining) _MakeReady(next, cycle);
  }
}

void CollectiveAllReduceTrafficManager::_Complete(int id, int cycle) {
  State & state = _state[id];
  if(state.complete) Error("Collective operation completed twice.");
  state.complete = true;
  ++_finished;
  _Release(_schedule.operations[id].after_receive, cycle);
}

void CollectiveAllReduceTrafficManager::_Inject() {
  for(int node = 0; node < _nodes; ++node) {
    if(!_partial_packets[node][0].empty()) continue;
    int last = _last_packet[node];
    if(last >= 0) {
      if(_state[last].generated == _schedule.operations[last].bytes / 128) {
        _state[last].injected = true;
        _Release(_schedule.operations[last].after_send, _time);
      }
      _last_packet[node] = -1;
    }
    if(_ready[node].empty()) continue;
    int id = _ready[node].front();
    _ready[node].pop_front();
    State & state = _state[id];
    allreduce::Operation const & op = _schedule.operations[id];
    if(!state.started) {
      state.started = true;
      state.mask = _values[node][op.first_chunk];
      for(int c = op.first_chunk; c < op.first_chunk + op.chunk_count; ++c)
        if(_values[node][c] != state.mask) Error("Collective message has inconsistent source chunks.");
      if(op.kind == allreduce::COPY && state.mask != _schedule.contributors)
        Error("AllGather started with an incomplete reduction.");
    }
    Packet packet = {id, state.generated++, state.mask};
    _packets.insert(std::make_pair(_cur_pid, packet));
    ++_packet_seq_no[node];
    ++_requestsOutstanding[node];
    _GeneratePacket(node, 1, 0, state.ready, op.destination, 8);
    _last_packet[node] = id;
    // Round-robin at packet boundaries shares the existing single injection
    // port among both 2D halves / all ready trees without inventing bandwidth.
    if(state.generated < op.bytes / 128) _ready[node].push_back(id);
  }
}

void CollectiveAllReduceTrafficManager::_RetireFlit(Flit * f, int destination) {
  std::map<int, Packet>::iterator it = _packets.find(f->pid);
  if(it == _packets.end()) Error("Unknown collective packet.");
  Packet packet = it->second;
  allreduce::Operation const & op = _schedule.operations[packet.operation];
  State & state = _state[packet.operation];
  if(op.destination != destination || op.source != f->src || packet.mask != state.mask)
    Error("Incorrect collective packet destination/source/contributors.");
  ++_received_flits;
  if(f->tail) {
    if(state.seen.empty()) state.seen.assign(op.bytes / 128, false);
    if(state.seen.at(packet.index)) Error("Duplicate collective packet.");
    state.seen[packet.index] = true;
    ++state.received;
    ++_received_packets;
    _packets.erase(it);
    if(state.received == op.bytes / 128) {
      if(!state.injected) Error("Collective message arrived before injection completed.");
      for(int c = op.first_chunk; c < op.first_chunk + op.chunk_count; ++c) {
        uint64_t & value = _values[destination][c];
        if(op.kind == allreduce::REDUCE) {
          if(value & state.mask) Error("Duplicate reduction contribution.");
          value |= state.mask;
        } else value = state.mask;
      }
      std::vector<bool>().swap(state.seen);
      _Complete(packet.operation, _time + 1);
    }
  }
  TrafficManager::_RetireFlit(f, destination);
}

bool CollectiveAllReduceTrafficManager::_SingleSim() {
  _sim_state = running;
  for(size_t id = 0; id < _state.size(); ++id)
    if(!_state[id].remaining && _state[id].ready < 0) _MakeReady(id, 0);
  while(_finished != int(_state.size())) {
    if(_time >= _max_cycles) Error("AllReduce exceeded allreduce_max_cycles.");
    _Step();
  }
  _drain_time = _time;
  _sim_state = draining;
  _Validate();
  std::cout << "AllReduce algorithm = " << _schedule.algorithm << '\n'
            << "AllReduce participating ranks = " << _schedule.ranks << '\n'
            << "AllReduce excluded node = " << _schedule.excluded << '\n'
            << "AllReduce input bytes per rank = " << _bytes << '\n'
            << "AllReduce padded bytes per rank = "
            << std::accumulate(_schedule.chunk_bytes.begin(), _schedule.chunk_bytes.end(), uint64_t(0)) << '\n'
            << "AllReduce data chunks = " << _schedule.chunk_bytes.size() << '\n'
            << "AllReduce packets = " << _received_packets << '\n'
            << "AllReduce flits = " << _received_flits << '\n'
            << "Collective completion = " << _time << " cycles" << std::endl;
  return true;
}

void CollectiveAllReduceTrafficManager::_Validate() {
  for(int node = 0; node < _nodes; ++node) {
    if(node != _schedule.excluded)
      for(uint64_t value : _values[node])
        if(value != _schedule.contributors) Error("Final AllReduce result is incomplete.");
    if(uint64_t(_sent_flits[0][node]) != _expected_sent[node] ||
       uint64_t(_accepted_flits[0][node]) != _expected_received[node] ||
       !_partial_packets[node][0].empty() || !_ready[node].empty() ||
       _requestsOutstanding[node]) Error("Collective per-node accounting mismatch.");
  }
  if(!_packets.empty() || !_total_in_flight_flits[0].empty() ||
     _received_flits != _expected_flits || _received_packets != _expected_packets)
    Error("Collective traffic accounting mismatch.");
  // Validate every directed physical link, rather than assuming one-hop traffic.
  std::vector<FlitChannel *> const & channels = _net[0]->GetChannels();
  std::vector<bool> checked(_nodes * 4, false);
  for(FlitChannel * channel : channels) {
    int index = 4*channel->GetSource()->GetID() + channel->GetSourcePort();
    if(index < 0 || index >= int(checked.size()) || checked[index] ||
       uint64_t(channel->GetActivity()[0]) != _expected_links[index])
      Error("Collective physical-link accounting mismatch.");
    checked[index] = true;
  }
  if(std::find(checked.begin(), checked.end(), false) != checked.end())
    Error("Collective link validation missed a channel.");
}
