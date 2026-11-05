#ifndef BOOKSIM_COLLECTIVE_ALLREDUCE_TRAFFIC_MANAGER_HPP
#define BOOKSIM_COLLECTIVE_ALLREDUCE_TRAFFIC_MANAGER_HPP

#include "allreduce_schedule.hpp"
#include "trafficmanager.hpp"

class CollectiveAllReduceTrafficManager : public TrafficManager {
  struct State {
    int remaining, ready, generated, received;
    bool started, injected, complete;
    uint64_t mask;
    std::vector<bool> seen;
    State() : remaining(0), ready(-1), generated(0), received(0),
              started(false), injected(false), complete(false), mask(0) {}
  };
  struct Packet { int operation, index; uint64_t mask; };

  allreduce::Schedule _schedule;
  int _bytes, _max_cycles, _finished;
  std::vector<State> _state;
  std::vector<std::deque<int> > _ready;
  std::vector<int> _last_packet;
  std::vector<std::vector<uint64_t> > _values;
  std::map<int, Packet> _packets;
  std::vector<uint64_t> _expected_sent, _expected_received, _expected_links;
  uint64_t _expected_flits, _expected_packets, _received_flits, _received_packets;

  void _MakeReady(int id, int cycle);
  void _Release(std::vector<int> const & successors, int cycle);
  void _Complete(int id, int cycle);
  void _Validate();
protected:
  void _Inject() override;
  void _RetireFlit(Flit * f, int destination) override;
  bool _SingleSim() override;
public:
  CollectiveAllReduceTrafficManager(Configuration const & config,
                                    std::vector<Network *> const & net);
};
#endif
