#ifndef EXPERIMENT_HARDWARE_HPP
#define EXPERIMENT_HARDWARE_HPP
#include "booksim_config.hpp"
#include <stdexcept>
#include "experiment_error.hpp"

namespace experiment {
// Additional fields live only in this external configuration subclass. The
// original BookSim configuration code and simulator core are never patched.
class ExperimentConfig : public BookSimConfig {
public:
  ExperimentConfig() {
    _int_map["experiment_flit_bytes"] = 32;
    _int_map["experiment_link_latency_ns"] = 21;
    _int_map["experiment_bandwidth_gbps"] = 25;
    _int_map["experiment_native_channels"] = 0;
  }
};

struct HardwareSpec {
  int flit_bytes = 32, packet_flits = 16, vcs = 4, vc_depth = 318;
  int link_latency_ns = 21, bandwidth_gbps = 25;
  int input_speedup = 1, hold_switch = 1;
  bool native_channels = false;
  HardwareSpec() = default;
  explicit HardwareSpec(Configuration const &config) {
    flit_bytes = config.GetInt("experiment_flit_bytes");
    packet_flits = config.GetInt("packet_size");
    vcs = config.GetInt("num_vcs");
    vc_depth = config.GetInt("vc_buf_size");
    link_latency_ns = config.GetInt("experiment_link_latency_ns");
    bandwidth_gbps = config.GetInt("experiment_bandwidth_gbps");
    native_channels = config.GetInt("experiment_native_channels") != 0;
    input_speedup = config.GetInt("input_speedup");
    hold_switch = config.GetInt("hold_switch_for_packet");
    if(flit_bytes <= 0 || flit_bytes > 256 || packet_flits <= 0 || packet_flits > 512 ||
       vcs <= 0 || vcs > 64 || vc_depth < packet_flits || link_latency_ns < 1 ||
       bandwidth_gbps < 1 || bandwidth_gbps > 256 || !config.GetInt("wait_for_tail_credit") ||
       config.GetInt("k") != 8 || config.GetInt("n") != 2 || config.GetStr("topology") != "mesh")
      Fail<std::invalid_argument>("Unsupported hardware profile or non-empty-VC admission");
  }
  int PacketBytes() const { return flit_bytes*packet_flits; }
  int LinkGbps() const { return native_channels ? flit_bytes : bandwidth_gbps; }
};
}
#endif
