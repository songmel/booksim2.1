#include "ringallreducetrafficmanager.hpp"

#include <algorithm>
#include <climits>

RingAllReduceTrafficManager::RingAllReduceTrafficManager(
    Configuration const & config, vector<Network *> const & net)
  : TrafficManager(config, net), _k(config.GetInt("k")),
    _bytes(config.GetInt("allreduce_bytes")),
    _flit_bytes(config.GetInt("flit_bytes")),
    _max_flits(config.GetInt("max_packet_flits")),
    _max_cycles(config.GetInt("allreduce_max_cycles")),
    _retired_packets_count(0), _retired_flits_count(0), _valid_bytes_count(0)
{
  if(config.GetStr("topology") != "mesh" || config.GetInt("n") != 2 ||
     _k != 8 || _nodes != 64 || _routers != _nodes ||
     config.GetStr("router") != "iq") {
    Error("This AllReduce experiment requires an 8x8 mesh with 64 ranks and IQ routers.");
  }
  if(_classes != 1 || _subnets != 1 || _use_read_write[0] ||
     _total_sims != 1 || config.GetStr("routing_function") != "dor" ||
     config.GetInt("c") != 1 || config.GetInt("link_failures") != 0) {
    Error("AllReduce requires one class/subnet/run, c=1, DOR and no faults or read/write traffic.");
  }
  if(_flit_bytes != 16 || _max_flits != 8 ||
     _vcs != 8 || config.GetInt("vc_buf_size") != 8 ||
     config.GetStr("buffer_policy") != "private") {
    Error("This experiment requires 16-byte flits, 8-flit packets and 8 VCs with 8-flit private buffers.");
  }
  if(_bytes <= 0 || _bytes % (_nodes * _flit_bytes * _max_flits)) {
    Error("AllReduce input bytes must be a positive multiple of 8192 so every chunk contains full packets.");
  }
  if(_max_cycles <= 0 || _max_cycles >= INT_MAX) {
    Error("Invalid AllReduce cycle limit.");
  }
  _chunk_bytes = _bytes / _nodes;
  _chunk_flits = _chunk_bytes / _flit_bytes;
  _packets_per_chunk = _chunk_flits / _max_flits;
  _steps = 2 * (_nodes - 1);
  if(uint64_t(_nodes) * _steps * _chunk_flits >= INT_MAX ||
     uint64_t(_steps) * _chunk_flits >= uint64_t(_max_cycles)) {
    Error("AllReduce exceeds the flit-ID range or configured cycle budget.");
  }
  _BuildMapping();
  _step.assign(_nodes, 0);
  _messages.assign(_nodes, vector<Message>(_steps));
  _masks.assign(_nodes, vector<uint64_t>(_nodes));
  for(int r = 0; r < _nodes; ++r) {
    _messages[r][0].ready = 0;
    fill(_masks[r].begin(), _masks[r].end(), uint64_t(1) << r);
  }
}

void RingAllReduceTrafficManager::_BuildMapping() {
  for(int x = 0; x < _k; ++x) _rank_to_node.push_back(x);
  for(int y = 1; y < _k; ++y) {
    if(y % 2) {
      for(int x = _k - 1; x >= 1; --x) _rank_to_node.push_back(x + _k*y);
    } else {
      for(int x = 1; x < _k; ++x) _rank_to_node.push_back(x + _k*y);
    }
  }
  for(int y = _k - 1; y >= 1; --y) _rank_to_node.push_back(_k*y);
  _node_to_rank.assign(_nodes, -1);
  for(int r = 0; r < _nodes; ++r) {
    int node = _rank_to_node[r];
    if(_node_to_rank[node] >= 0) Error("Duplicate rank mapping.");
    _node_to_rank[node] = r;
    int next = _rank_to_node[(r + 1) % _nodes];
    int hops = abs(node % _k - next % _k) + abs(node / _k - next / _k);
    if(hops != 1) Error("Non-adjacent Ring edge.");
  }
}

int RingAllReduceTrafficManager::_SendChunk(int rank, int step) const {
  int index = step < _nodes - 1 ? rank - step : rank + 1 - (step - (_nodes - 1));
  return (index % _nodes + _nodes) % _nodes;
}

int RingAllReduceTrafficManager::_ReceiveChunk(int rank, int step) const {
  return _SendChunk((rank + _nodes - 1) % _nodes, step);
}

void RingAllReduceTrafficManager::_Inject() {
  for(int rank = 0; rank < _nodes; ++rank) {
    int step = _step[rank];
    if(step == _steps) continue;
    int node = _rank_to_node[rank];
    Message & message = _messages[rank][step];
    if(_partial_packets[node][0].empty() &&
       message.generated_packets < _packets_per_chunk) {
      int index = message.generated_packets++;
      int chunk = _SendChunk(rank, step);
      Packet packet = {rank, step, index, chunk, _masks[rank][chunk]};
      _packets.insert(make_pair(_cur_pid, packet));
      ++_packet_seq_no[node];
      ++_requestsOutstanding[node];
      _GeneratePacket(node, 1, 0, message.ready,
                      _rank_to_node[(rank + 1) % _nodes], _max_flits);
    }
  }
}

void RingAllReduceTrafficManager::_RetireFlit(Flit * f, int dest) {
  map<int, Packet>::iterator iter = _packets.find(f->pid);
  if(iter == _packets.end()) Error("Received an unknown AllReduce packet.");
  Packet const & packet = iter->second;
  int rank = _node_to_rank[dest];
  if(rank != (packet.rank + 1) % _nodes ||
     packet.chunk != _ReceiveChunk(rank, packet.step)) {
    Error("Incorrect Ring destination or chunk.");
  }
  ++_retired_flits_count;
  if(f->tail) {
    Message & message = _messages[rank][packet.step];
    if(message.received_packets >= _packets_per_chunk) Error("Duplicate message.");
    if(message.seen.empty()) message.seen.assign(_packets_per_chunk, false);
    if(message.seen[packet.index]) Error("Duplicate packet index.");
    message.seen[packet.index] = true;
    if(message.received_packets && message.received_mask != packet.mask) {
      Error("Packets in one chunk have inconsistent contributor masks.");
    }
    message.received_mask = packet.mask;
    ++message.received_packets;
    ++_retired_packets_count;
    _valid_bytes_count += _max_flits * _flit_bytes;
    if(message.received_packets == _packets_per_chunk) vector<bool>().swap(message.seen);
    _packets.erase(iter);
  }
  TrafficManager::_RetireFlit(f, dest);
}

bool RingAllReduceTrafficManager::_AdvanceRanks() {
  int completed = 0;
  for(int r = 0; r < _nodes; ++r) {
    int step = _step[r];
    if(step == _steps) {
      ++completed;
      continue;
    }
    Message & message = _messages[r][step];
    if(_sent_flits[0][_rank_to_node[r]] != (step + 1) * _chunk_flits ||
       message.received_packets != _packets_per_chunk) continue;
    int chunk = _ReceiveChunk(r, step);
    uint64_t mask = message.received_mask;
    if(step < _nodes - 1) {
      if(mask & _masks[r][chunk]) Error("Duplicate reduction contribution.");
      _masks[r][chunk] |= mask;
    } else {
      if(mask != ~uint64_t(0)) Error("AllGather received an incomplete reduction.");
      _masks[r][chunk] = mask;
    }
    ++_step[r];
    if(_step[r] == _steps) ++completed;
    else _messages[r][_step[r]].ready = _time;
  }
  return completed == _nodes;
}

bool RingAllReduceTrafficManager::_SingleSim() {
  _sim_state = running;
  while(!_AdvanceRanks()) {
    if(_time >= _max_cycles) Error("AllReduce exceeded allreduce_max_cycles.");
    _Step();
  }
  _drain_time = _time;
  _sim_state = draining;
  for(int r = 0; r < _nodes; ++r) {
    for(int chunk = 0; chunk < _nodes; ++chunk) {
      if(_masks[r][chunk] != ~uint64_t(0)) Error("Final AllReduce result is incomplete.");
    }
    int node = _rank_to_node[r];
    if(_sent_flits[0][node] != _steps * _chunk_flits ||
       _accepted_flits[0][node] != _steps * _chunk_flits ||
       _sent_packets[0][node] != _steps * _packets_per_chunk ||
       _accepted_packets[0][node] != _steps * _packets_per_chunk) {
      Error("AllReduce per-rank traffic accounting mismatch.");
    }
  }
  if(!_packets.empty() || !_total_in_flight_flits[0].empty() ||
     _retired_packets_count != uint64_t(_nodes) * _steps * _packets_per_chunk ||
     _retired_flits_count != uint64_t(_nodes) * _steps * _chunk_flits ||
     _valid_bytes_count != uint64_t(_nodes) * _steps * _chunk_bytes ||
     _time < _steps * _chunk_flits) {
    Error("AllReduce traffic or serialization-bound validation failed.");
  }
  _ValidateLinks();
  return true;
}

void RingAllReduceTrafficManager::_ValidateLinks() {
  uint64_t link_flits = 0;
  int active = 0, physical = 0;
  vector<FlitChannel *> const & channels = _net[0]->GetChannels();
  for(size_t i = 0; i < channels.size(); ++i) {
    int source = channels[i]->GetSource()->GetID();
    int flits = channels[i]->GetActivity()[0];
    // KNCube allocates wraparound channels even for a mesh; DOR never uses them.
    int port = channels[i]->GetSourcePort();
    bool wrap = (port == 0 && source % _k == _k - 1) ||
                (port == 1 && source % _k == 0) ||
                (port == 2 && source / _k == _k - 1) ||
                (port == 3 && source / _k == 0);
    if(wrap) {
      if(flits) Error("Traffic used a wraparound link in mesh.");
      continue;
    }
    ++physical;
    active += flits > 0;
    link_flits += flits;
  }
  if(physical != 4 * _k * (_k - 1) ||
     active != _nodes || link_flits != _retired_flits_count) {
    Error("Physical link accounting mismatch.");
  }
}
