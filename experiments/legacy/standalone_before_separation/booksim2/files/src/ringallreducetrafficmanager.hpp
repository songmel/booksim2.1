#ifndef _RINGALLREDUCETRAFFICMANAGER_HPP_
#define _RINGALLREDUCETRAFFICMANAGER_HPP_

#include <cstdint>
#include "trafficmanager.hpp"

// One Ring AllReduce on an 8x8 mesh, with whole-chunk dependencies.
// Statistics and output are provided by the existing TrafficManager.
class RingAllReduceTrafficManager : public TrafficManager {
  struct Message {
    int ready, generated_packets, received_packets;
    uint64_t received_mask;
    vector<bool> seen;
    Message() : ready(-1), generated_packets(0), received_packets(0), received_mask(0) {}
  };
  struct Packet {
    int rank, step, index, chunk;
    uint64_t mask;
  };

  int _k, _bytes, _flit_bytes, _max_flits, _chunk_bytes;
  int _chunk_flits, _packets_per_chunk, _steps, _max_cycles;
  vector<int> _rank_to_node, _node_to_rank, _step;
  vector<vector<Message> > _messages;
  vector<vector<uint64_t> > _masks;
  map<int, Packet> _packets;
  uint64_t _retired_packets_count, _retired_flits_count, _valid_bytes_count;

  void _BuildMapping();
  int _SendChunk(int rank, int step) const;
  int _ReceiveChunk(int rank, int step) const;
  bool _AdvanceRanks();
  void _ValidateLinks();

protected:
  virtual void _Inject();
  virtual void _RetireFlit(Flit * f, int dest);
  virtual bool _SingleSim();

public:
  RingAllReduceTrafficManager(Configuration const & config,
                              vector<Network *> const & net);
};

#endif
