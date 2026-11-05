#ifndef EXPERIMENT_TABLE_MESH_HPP
#define EXPERIMENT_TABLE_MESH_HPP
#include "network.hpp"
#include "phase_profile.hpp"
#include "experiment_hardware.hpp"
#include <algorithm>
#include <array>
#include <cstdint>
#include <deque>
#include <stdexcept>

namespace experiment {
// Physical time inside the serializer uses 40 ps ticks: 25 ticks/ns,
// 32 ticks/flit. Routers still run at exactly 1 GHz, without extra core steps.
constexpr int FLIT_BYTES = 32;
constexpr int PACKET_BYTES = 512;
constexpr int PACKET_FLITS = PACKET_BYTES/FLIT_BYTES;
constexpr int VCS = 4;
constexpr int VC_DEPTH = 318;

class RateChannel : public FlitChannel {
  struct Transit { int64_t ready; Flit *flit; };
  std::deque<Transit> transit_;
  int64_t next_wire_tick_ = 0;
  HardwareSpec hardware_;
  std::vector<int> outstanding_, packet_, sent_in_packet_;
  uint64_t heads_ = 0;
  int max_outstanding_ = 0;
public:
  RateChannel(Module *parent, const std::string &name, int propagation_ns,
              HardwareSpec const &hardware = HardwareSpec())
    : FlitChannel(parent, name, 1), hardware_(hardware),
      outstanding_(hardware.vcs, 0), packet_(hardware.vcs, -1), sent_in_packet_(hardware.vcs, 0) {
    SetLatency(propagation_ns);
  }
  static int64_t FinishTick(int64_t now, int64_t next, int flit_bytes = 32) {
    return std::max(now, next)+flit_bytes;
  }
  void Send(Flit *f) override {
    FlitChannel::Send(f);
    PhaseScope phase(LINK_PHASE);
    _input = nullptr;
    if(!f) return;
    const int vc = f->vc;
    if(vc < 0 || vc >= hardware_.vcs) Fail("Invalid channel VC");
    if(f->head) {
      // Existing wait_for_tail_credit guarantees an empty downstream VC.
      // Thus all 16 flits are admitted together into its 318-flit capacity.
      if(outstanding_[vc] || sent_in_packet_[vc])
        Fail("VCT head admitted without an empty reserved VC");
      packet_[vc] = f->pid;
      ++heads_;
    }
    if(packet_[vc] != f->pid || sent_in_packet_[vc] >= hardware_.packet_flits)
      Fail("VCT packet reservation mismatch");
    ++sent_in_packet_[vc];
    ++outstanding_[vc];
    max_outstanding_ = std::max(max_outstanding_, outstanding_[vc]);
    if(f->tail) {
      if(sent_in_packet_[vc] != hardware_.packet_flits)
        Fail("Unexpected packet length");
      sent_in_packet_[vc] = 0;
    }
    const int ticks_per_ns = hardware_.bandwidth_gbps;
    next_wire_tick_ = FinishTick(int64_t(GetSimTime())*ticks_per_ns, next_wire_tick_, hardware_.flit_bytes);
    // Whole flit arrival = serialization end + propagation. Publish one
    // cycle earlier because the receiving router samples on the next edge.
    const int64_t ready = (next_wire_tick_+int64_t(GetLatency())*ticks_per_ns+ticks_per_ns-1)/ticks_per_ns-1;
    transit_.push_back({ready, f});
  }
  void ReadInputs() override {}
  void WriteOutputs() override {
    PhaseScope phase(LINK_PHASE);
    _output = nullptr;
    if(!transit_.empty() && transit_.front().ready <= GetSimTime()) {
      _output = transit_.front().flit;
      transit_.pop_front();
    }
  }
  void ReturnCredit(Credit const *credit) {
    PhaseScope phase(LINK_PHASE);
    for(auto vc : credit->vc) {
      if(vc < 0 || vc >= hardware_.vcs || outstanding_[vc] <= 0)
        Fail("Unmatched channel credit");
      --outstanding_[vc];
    }
  }
  void Validate() const {
    if(!transit_.empty() || max_outstanding_ > hardware_.packet_flits)
      Fail("Undrained channel or broken packet reservation");
    for(int vc = 0; vc < hardware_.vcs; ++vc)
      if(outstanding_[vc] || sent_in_packet_[vc])
        Fail("Undrained VCT reservation");
  }
  uint64_t Heads() const { return heads_; }
  int MaxOutstanding() const { return max_outstanding_; }
};

class AuditedCreditChannel : public CreditChannel {
  RateChannel *forward_;
public:
  AuditedCreditChannel(Module *parent, const std::string &name, RateChannel *forward)
    : CreditChannel(parent, name), forward_(forward) { SetLatency(forward->GetLatency()); }
  void WriteOutputs() override {
    CreditChannel::WriteOutputs();
    if(_output) forward_->ReturnCredit(_output);
  }
};

class TableMesh : public Network {
  HardwareSpec hardware_;
  void ReplaceChannels(std::vector<FlitChannel *> &flits,
                       std::vector<CreditChannel *> &credits, int latency) {
    for(size_t i = 0; i < flits.size(); ++i) {
      auto *old_flit = flits[i];
      auto *old_credit = credits[i];
      auto *flit = new RateChannel(this, old_flit->Name(), latency, hardware_);
      auto *credit = new AuditedCreditChannel(this, old_credit->Name(), flit);
      for(auto &module : _timed_modules) {
        if(module == old_flit) module = flit;
        else if(module == old_credit) module = credit;
      }
      flits[i] = flit;
      credits[i] = credit;
      delete old_flit;
      delete old_credit;
    }
  }
  void _ComputeSize(Configuration const &) override {
    gK = 8; gN = 2; gC = 1;
    _size = _nodes = 64;
    _channels = 256;
  }
  void _BuildNet(Configuration const &config) override {
    for(int node = 0; node < 64; ++node) {
      _routers[node] = Router::NewRouter(config, this, "router"+std::to_string(node), node, 5, 5);
      _timed_modules.push_back(_routers[node]);
      for(int dimension = 0; dimension < 2; ++dimension) {
        int stride = dimension ? 8 : 1;
        int coordinate = (node/stride)%8;
        int left = node+(coordinate ? -stride : 7*stride);
        int right = node+(coordinate < 7 ? stride : -7*stride);
        int positive = 4*node+2*dimension;
        int negative = positive+1;
        int input_positive = 4*right+2*dimension+1;
        int input_negative = 4*left+2*dimension;
        _routers[node]->AddInputChannel(_chan[input_positive], _chan_cred[input_positive]);
        _routers[node]->AddInputChannel(_chan[input_negative], _chan_cred[input_negative]);
        _routers[node]->AddOutputChannel(_chan[positive], _chan_cred[positive]);
        _routers[node]->AddOutputChannel(_chan[negative], _chan_cred[negative]);
      }
      _routers[node]->AddInputChannel(_inject[node], _inject_cred[node]);
      _routers[node]->AddOutputChannel(_eject[node], _eject_cred[node]);
    }
  }
public:
  explicit TableMesh(Configuration const &config, HardwareSpec const &hardware = HardwareSpec())
    : Network(config, "table_mesh"), hardware_(hardware) {
    _ComputeSize(config);
    _Alloc();
    if(hardware_.native_channels) {
      for(auto *channel : _chan) channel->SetLatency(hardware_.link_latency_ns);
      for(auto *channel : _chan_cred) channel->SetLatency(hardware_.link_latency_ns);
    } else {
      ReplaceChannels(_inject, _inject_cred, 1);
      ReplaceChannels(_eject, _eject_cred, 1);
      ReplaceChannels(_chan, _chan_cred, hardware_.link_latency_ns);
    }
    _BuildNet(config);
  }
  void Validate() const {
    if(hardware_.native_channels) return; // Native Run already drained all flits/credits.
    for(auto const *group : {&_inject, &_eject, &_chan})
      for(auto *channel : *group) static_cast<RateChannel *>(channel)->Validate();
  }
};
}
#endif
