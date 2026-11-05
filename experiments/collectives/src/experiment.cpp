#include "booksim_config.hpp"
#include "trafficmanager.hpp"
#include "schedule.hpp"
#include "table_mesh.hpp"
#include <chrono>
#include <climits>
#include <iomanip>
#include <memory>
#include <numeric>
#include <sstream>

TrafficManager *trafficManager = nullptr;
bool gPrintActivity = false;
bool gTrace = false;
int gK = 8, gN = 2, gC = 1, gNodes = 64;
std::ostream *gWatchOut = nullptr;
int GetSimTime() { return trafficManager ? trafficManager->getTime() : 0; }
Stats *GetStats(std::string const &name) { return trafficManager->getStats(name); }

namespace experiment {
using Clock = std::chrono::steady_clock;
static double Seconds(Clock::time_point start) {
  return std::chrono::duration<double>(Clock::now()-start).count();
}
static void Require(bool condition, const char *message) {
  if(!condition) Fail(message);
}
struct State {
  int remaining = 0, ready = -1, generated = 0, received = 0;
  bool started = false, injected = false, complete = false;
  uint64_t mask = 0;
  std::vector<uint64_t> seen;
};
struct Packet { int operation, index; };

// Shared collective injector, receiver and validator for every workload.
// Native _Step, routing, allocation, flow control, and statistics are reused.
class Collective : public TrafficManager {
  HardwareSpec hardware_;
  Schedule plan_;
  std::vector<State> state_;
  std::vector<std::deque<int>> ready_;
  std::vector<int> last_packet_;
  std::vector<std::vector<uint64_t>> values_;
  std::deque<Packet> packet_storage_;
  std::vector<Packet *> free_packets_;
  std::vector<uint64_t> expected_sent_, expected_received_, expected_links_;
  uint64_t expected_flits_ = 0, received_flits_ = 0, received_packets_ = 0;
  uint64_t retirement_hash_ = 1469598103934665603ULL;
  int finished_ = 0, max_cycles_, input_bytes_;
  int completion_cycles_ = -1;
  double collective_seconds_ = 0, validation_seconds_ = 0;
  void Hash(uint64_t value) { retirement_hash_ = (retirement_hash_^value)*1099511628211ULL; }
  void MakeReady(int id, int cycle) {
    State &state = state_[id];
    Require(state.ready < 0, "Operation released twice");
    state.ready = cycle;
    if(plan_.operations[id].kind == BARRIER) Complete(id, cycle);
    else ready_[plan_.operations[id].source].push_back(id);
  }
  void Release(std::vector<int> const &successors, int cycle) {
    for(int next : successors) {
      Require(state_[next].remaining > 0, "Dependency released twice");
      if(!--state_[next].remaining) MakeReady(next, cycle);
    }
  }
  void Complete(int id, int cycle) {
    Require(!state_[id].complete, "Operation completed twice");
    state_[id].complete = true;
    ++finished_;
    Release(plan_.operations[id].after_receive, cycle);
  }
  void Generate(int id) {
    Operation const &op = plan_.operations[id];
    State &state = state_[id];
    Packet *packet;
    if(free_packets_.empty()) {
      packet_storage_.push_back({id, state.generated});
      packet = &packet_storage_.back();
    } else {
      packet = free_packets_.back();
      free_packets_.pop_back();
      *packet = {id, state.generated};
    }
    ++state.generated;
    // Preserve the native-equivalent generation/registration work (including
    // OPT1's in-flight maps) in the BookSim domain, not the controller domain.
    PhaseScope native(BOOKSIM_PHASE);
    int pid = _cur_pid++;
    ++_packet_seq_no[op.source];
    ++_requestsOutstanding[op.source];
    // Only the experiment-side generator is new. Registration mirrors the
    // original four-argument _GeneratePacket API, without modifying its code.
    for(int index = 0; index < hardware_.packet_flits; ++index) {
      Flit *f = Flit::New();
      f->id = _cur_id++;
      f->pid = pid;
      f->subnetwork = 0;
      f->src = op.source;
      f->ctime = state.ready;
      f->record = true;
      f->cl = 0;
      f->type = Flit::ANY_TYPE;
      f->head = index == 0;
      f->tail = index == hardware_.packet_flits-1;
      f->dest = f->head ? op.destination : -1;
      f->pri = 0;
      f->vc = -1;
      f->data = packet;
      _total_in_flight_flits[0].insert(std::make_pair(f->id, f));
      _measured_in_flight_flits[0].insert(std::make_pair(f->id, f));
      _partial_packets[op.source][0].push_back(f);
    }
  }
  int _IssuePacket(int node, int cl) override {
      PhaseScope controller(ALLREDUCE_PHASE);
      // The stock 2.0 _Inject is non-virtual. Use its existing virtual
      // _IssuePacket hook, and suppress its synthetic packet generation.
      // Advance the source clock so its catch-up loop visits us once/cycle.
      _qtime[node][cl] = _time;
      int last = last_packet_[node];
      if(last >= 0) {
        if(state_[last].generated == plan_.operations[last].bytes/hardware_.PacketBytes()) {
          Require(!state_[last].injected, "Message injected twice");
          state_[last].injected = true;
          Release(plan_.operations[last].after_send, _time);
        }
        last_packet_[node] = -1;
      }
      if(ready_[node].empty()) return 0;
      int id = ready_[node].front();
      ready_[node].pop_front();
      State &state = state_[id];
      Operation const &op = plan_.operations[id];
      if(!state.started) {
        state.started = true;
        if(op.kind == EXCHANGE) {
          Require(op.first_chunk == node && op.chunk_count == 1,
                  "Invalid AllToAll source receive-slot mapping");
          state.mask = ExchangeToken(node, op.destination);
        } else {
          state.mask = values_[node][op.first_chunk];
          for(int chunk = op.first_chunk; chunk < op.first_chunk+op.chunk_count; ++chunk)
            Require(values_[node][chunk] == state.mask, "Inconsistent message chunk contributors");
        }
        if(op.kind == COPY) Require(state.mask == plan_.contributors, "Gather before complete reduction");
      }
      Generate(id);
      last_packet_[node] = id;
      if(state.generated < op.bytes/hardware_.PacketBytes()) ready_[node].push_back(id);
      return 0;
  }
  void _RetireFlit(Flit *f, int destination) override {
    PhaseScope controller(ALLREDUCE_PHASE);
    Packet *packet = static_cast<Packet *>(f->data);
    Operation const &op = plan_.operations[packet->operation];
    State &state = state_[packet->operation];
    Require(f->src == op.source && destination == op.destination, "Wrong packet source or destination");
    ++received_flits_;
    bool tail = f->tail;
    if(tail) {
      Require(!state.complete, "Packet arrived after message completion");
      if(state.seen.empty()) state.seen.assign((op.bytes/hardware_.PacketBytes()+63)/64, 0);
      auto &word = state.seen.at(packet->index/64);
      uint64_t bit = uint64_t(1) << (packet->index%64);
      Require(!(word & bit), "Duplicate packet");
      word |= bit;
      ++state.received;
      ++received_packets_;
      Hash(uint64_t(f->pid)); Hash(uint64_t(f->atime)); Hash(uint64_t(f->hops));
      if(state.received == op.bytes/hardware_.PacketBytes()) {
        for(int chunk = op.first_chunk; chunk < op.first_chunk+op.chunk_count; ++chunk) {
          uint64_t &value = values_[destination][chunk];
          if(op.kind == REDUCE) {
            Require(!(value & state.mask), "Duplicate contributor in reduction");
            value |= state.mask;
          } else if(op.kind == EXCHANGE) {
            Require(value == 0 && state.mask == ExchangeToken(op.source, destination),
                    "Duplicate or incorrect AllToAll receive block");
            value = state.mask;
          } else value = state.mask;
        }
        std::vector<uint64_t>().swap(state.seen);
        Complete(packet->operation, _time+1);
      }
    }
    {
      PhaseScope native(BOOKSIM_PHASE);
      TrafficManager::_RetireFlit(f, destination);
    }
    if(tail) free_packets_.push_back(packet);
  }
  bool _SingleSim() override {
    PhaseScope controller(ALLREDUCE_PHASE);
    _sim_state = running;
    auto start = Clock::now();
    for(size_t id = 0; id < state_.size(); ++id)
      if(!state_[id].remaining && state_[id].ready < 0) MakeReady(int(id), 0);
    while(finished_ != int(state_.size())) {
      Require(_time < max_cycles_, "Collective cycle budget exceeded");
      PhaseScope native(BOOKSIM_PHASE);
      _Step();
    }
    collective_seconds_ = Seconds(start);
    completion_cycles_ = _time;
    _drain_time = _time;
    _sim_state = draining;
    return true;
  }
public:
  void DisplayStats(std::ostream &out = std::cout) const override {
    PhaseScope output(OUTPUT_PHASE);
    TrafficManager::DisplayStats(out);
  }
  void DisplayOverallStats(std::ostream &out = std::cout) const override {
    PhaseScope output(OUTPUT_PHASE);
    TrafficManager::DisplayOverallStats(out);
  }
  Collective(Configuration const &config, std::vector<Network *> const &net,
             Schedule plan, int bytes, int max_cycles, HardwareSpec const &hardware = HardwareSpec())
    : TrafficManager(config, net), hardware_(hardware), plan_(std::move(plan)),
      max_cycles_(max_cycles), input_bytes_(bytes) {
    Require(_nodes == 64 && _vcs == hardware_.vcs && config.GetInt("vc_buf_size") == hardware_.vc_depth,
            "Experiment hardware mismatch");
    Require(config.GetInt("wait_for_tail_credit"), "Empty-VC admission is required");
    state_.resize(plan_.operations.size());
    ready_.resize(64); last_packet_.assign(64, -1);
    values_.assign(64, std::vector<uint64_t>(plan_.chunk_bytes.size()));
    expected_sent_.assign(64, 0); expected_received_.assign(64, 0); expected_links_.assign(256, 0);
    for(int node = 0; node < 64; ++node) {
      if(plan_.algorithm == "alltoall") values_[node][node] = ExchangeToken(node, node);
      else std::fill(values_[node].begin(), values_[node].end(), plan_.contributors & (uint64_t(1)<<node));
    }
    for(size_t id = 0; id < state_.size(); ++id) {
      Operation const &op = plan_.operations[id];
      state_[id].remaining = op.dependencies;
      if(op.kind == BARRIER) continue;
      Require(op.bytes > 0 && op.bytes%hardware_.PacketBytes() == 0, "Unaligned operation");
      uint64_t flits = op.bytes/hardware_.flit_bytes;
      expected_flits_ += flits;
      expected_sent_[op.source] += flits;
      expected_received_[op.destination] += flits;
      int node = op.source;
      while(node%8 != op.destination%8) {
        int port = node%8 < op.destination%8 ? 0 : 1;
        expected_links_[4*node+port] += flits;
        node += port ? -1 : 1;
      }
      while(node/8 != op.destination/8) {
        int port = node/8 < op.destination/8 ? 2 : 3;
        expected_links_[4*node+port] += flits;
        node += port == 2 ? 8 : -8;
      }
    }
    Require(expected_flits_ < INT_MAX, "Flit-ID capacity exceeded");
  }
  void Validate() {
    auto start = Clock::now();
    Require(received_flits_ == expected_flits_ && received_packets_*hardware_.packet_flits == expected_flits_,
            "Global traffic accounting mismatch");
    Require(free_packets_.size() == packet_storage_.size(), "Unretired packet metadata");
    for(int node = 0; node < 64; ++node) {
      if(node != plan_.excluded)
        for(size_t chunk = 0; chunk < values_[node].size(); ++chunk) {
          uint64_t expected = plan_.algorithm == "alltoall" ? ExchangeToken(int(chunk), node) : plan_.contributors;
          Require(values_[node][chunk] == expected, "Incomplete or incorrectly placed collective result");
        }
      Require(uint64_t(_sent_flits[0][node]) == expected_sent_[node] &&
              uint64_t(_accepted_flits[0][node]) == expected_received_[node] &&
              _partial_packets[node][0].empty() && ready_[node].empty() && !_requestsOutstanding[node],
              "Endpoint traffic accounting mismatch");
    }
    Require(_total_in_flight_flits[0].empty() && Credit::OutStanding() == 0,
            "Network not fully drained");
    std::vector<bool> checked(256, false);
    for(FlitChannel *channel : _net[0]->GetChannels()) {
      int index = 4*channel->GetSource()->GetID()+channel->GetSourcePort();
      Require(index >= 0 && index < 256 && !checked[index], "Invalid physical channel map");
      Require(uint64_t(channel->GetActivity()[0]) == expected_links_[index], "Physical channel traffic mismatch");
      checked[index] = true;
    }
    Require(std::find(checked.begin(), checked.end(), false) == checked.end(), "Unchecked physical channel");
    static_cast<TableMesh *>(_net[0])->Validate();
    validation_seconds_ = Seconds(start);
  }
  std::string Result(double setup_seconds, double run_seconds) const {
    std::ostringstream out;
    out << std::setprecision(17)
        << "{\"algorithm\":\"" << plan_.algorithm << "\",\"input_bytes\":" << input_bytes_
        << ",\"padded_bytes\":" << std::accumulate(plan_.chunk_bytes.begin(), plan_.chunk_bytes.end(), uint64_t(0))
        << ",\"active_ranks\":" << plan_.ranks << ",\"excluded_node\":" << plan_.excluded
        << ",\"flit_bytes\":" << hardware_.flit_bytes << ",\"packet_flits\":" << hardware_.packet_flits
        << ",\"packet_bytes\":" << hardware_.PacketBytes() << ",\"num_vcs\":" << hardware_.vcs
        << ",\"vc_depth\":" << hardware_.vc_depth << ",\"link_latency_ns\":" << hardware_.link_latency_ns
        << ",\"bandwidth_gbps\":" << hardware_.LinkGbps()
        << ",\"channel_model\":\"" << (hardware_.native_channels ? "native" : "rate") << "\""
        << ",\"network_topology\":\"mesh\""
        << ",\"communication_schedule\":\"" << (plan_.algorithm == "alltoall" ? "direct_exchange" : plan_.algorithm) << "\""
        << ",\"input_size_semantics\":\"total buffer per rank; AllToAll includes self block\""
        << ",\"network_bytes_per_rank\":" << expected_sent_[0]*hardware_.flit_bytes
        << ",\"peer_block_bytes\":" << (plan_.algorithm == "alltoall" ? input_bytes_/plan_.ranks : 0)
        << ",\"self_copy_bytes_per_rank\":" << (plan_.algorithm == "alltoall" ? input_bytes_/plan_.ranks : 0)
        << ",\"input_speedup\":" << hardware_.input_speedup << ",\"hold_switch\":" << hardware_.hold_switch
        << ",\"collective_cycles\":" << completion_cycles_ << ",\"drain_cycles\":" << _time
        << ",\"setup_seconds\":" << setup_seconds << ",\"run_seconds\":" << run_seconds
        << ",\"collective_seconds\":" << collective_seconds_ << ",\"validation_seconds\":" << validation_seconds_
        << ",\"packets\":" << received_packets_ << ",\"flits\":" << received_flits_
        << ",\"link_flits\":" << std::accumulate(expected_links_.begin(), expected_links_.end(), uint64_t(0))
        << ",\"retirement_hash\":\"" << std::hex << retirement_hash_ << std::dec << "\""
        << ",\"packet_latency\":" << _plat_stats[0]->Average()
        << ",\"network_latency\":" << _nlat_stats[0]->Average()
        << ",\"flit_latency\":" << _flat_stats[0]->Average()
        << ",\"average_hops\":" << _hop_stats[0]->Average()
        << ",\"validation_passed\":true";
    auto const &profile = PhaseProfile::Instance();
    if(profile.Total()) {
      out << ",\"timing_method\":\"SIGPROF CPU sampling; estimates, not exact phase timers\""
          << ",\"profile_period_us\":" << profile.PeriodUs()
          << ",\"profile_cpu_seconds\":" << profile.CpuSeconds()
          << ",\"profile_samples\":" << profile.Total()
          << ",\"observed_cpu_us_per_sample\":" << profile.CpuSeconds()*1e6/profile.Total()
          << ",\"booksim_samples\":" << profile.Samples(BOOKSIM_PHASE)
          << ",\"allreduce_samples\":" << profile.Samples(ALLREDUCE_PHASE)
          << ",\"link_model_samples\":" << profile.Samples(LINK_PHASE)
          << ",\"output_samples\":" << profile.Samples(OUTPUT_PHASE)
          << ",\"booksim_cpu_seconds_estimate\":" << profile.Estimate(BOOKSIM_PHASE)
          << ",\"allreduce_cpu_seconds_estimate\":" << profile.Estimate(ALLREDUCE_PHASE)
          << ",\"collective_controller_cpu_seconds_estimate\":" << profile.Estimate(ALLREDUCE_PHASE)
          << ",\"link_model_cpu_seconds_estimate\":" << profile.Estimate(LINK_PHASE)
          << ",\"output_cpu_seconds_estimate\":" << profile.Estimate(OUTPUT_PHASE);
    }
    out << "}";
    return out.str();
  }
};
}

static int RunExperiment(int argc, char **argv) {
    if(argc < 6 || argc > 7) {
      std::cerr << "Usage: collective CONFIG ALGORITHM INPUT_BYTES STRIPE_BYTES MAX_CYCLES [--schedule-only|--profile-us=N]\n";
      return 2;
    }
    auto setup = experiment::Clock::now();
    std::string algorithm = argv[2];
    const int bytes = experiment::ParseInt(argv[3]), stripe = experiment::ParseInt(argv[4]), max_cycles = experiment::ParseInt(argv[5]);
    if(bytes <= 0 || bytes > 128*1024*1024 || stripe <= 0 || max_cycles <= 0)
      experiment::Fail<std::invalid_argument>("Input must be within (0,128 MiB]; stripe and cycle budget must be positive");
    experiment::ExperimentConfig config;
    config.ParseFile(argv[1]);
    experiment::HardwareSpec hardware(config);
    if(algorithm != "ring1d" && algorithm != "alltoall")
      experiment::Fail<std::invalid_argument>("Only Ring1D AllReduce and AllToAll are supported");
    experiment::Schedule plan = algorithm == "ring1d" ? experiment::Ring1D(8, bytes, hardware.PacketBytes()) :
      experiment::AllToAll(8, bytes, hardware.PacketBytes());
    int profile_us = 0;
    if(argc == 7 && std::string(argv[6]) == "--schedule-only") {
      std::cout << "{\"algorithm\":\"" << algorithm << "\",\"operations\":" << plan.operations.size()
                << ",\"chunks\":" << plan.chunk_bytes.size() << "}\n";
      return 0;
    }
    if(argc == 7) {
      std::string option = argv[6];
      if(option.compare(0, 13, "--profile-us=") != 0) experiment::Fail<std::invalid_argument>("Unknown option");
      profile_us = experiment::ParseInt(option.substr(13));
      if(profile_us < 1000) experiment::Fail<std::invalid_argument>("CPU profile period must be at least 1000 us");
    }
    InitializeRoutingMap(config);
    std::unique_ptr<experiment::TableMesh> network(new experiment::TableMesh(config, hardware));
    std::unique_ptr<experiment::Collective> collective(new experiment::Collective(
      config, std::vector<Network *>{network.get()}, std::move(plan), bytes, max_cycles, hardware));
    trafficManager = collective.get();
    double setup_seconds = experiment::Seconds(setup);
    auto run = experiment::Clock::now();
    if(profile_us) experiment::PhaseProfile::Instance().Start(profile_us);
    bool ok = collective->Run();
    if(profile_us) experiment::PhaseProfile::Instance().Stop();
    double run_seconds = experiment::Seconds(run);
    experiment::Require(ok, "Native traffic manager failed");
    collective->Validate();
    std::cout << "EXPERIMENT_RESULT " << collective->Result(setup_seconds, run_seconds) << std::endl;
    collective.reset();
    trafficManager = nullptr;
    network.reset();
    return 0;
}

int main(int argc, char **argv) {
#if defined(__EXCEPTIONS)
  try {
    return RunExperiment(argc, argv);
  } catch(std::exception const &error) {
    std::cerr << "EXPERIMENT_ERROR " << error.what() << std::endl;
    return 1;
  }
#else
  return RunExperiment(argc, argv);
#endif
}
