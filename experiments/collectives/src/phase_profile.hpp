#ifndef EXPERIMENT_PHASE_PROFILE_HPP
#define EXPERIMENT_PHASE_PROFILE_HPP

#include <array>
#include <cerrno>
#include <csignal>
#include <ctime>
#include <stdexcept>
#include <sys/time.h>
#include "experiment_error.hpp"

namespace experiment {
// These are execution domains, not simulated network times. No clock is read
// on a flit/channel callback. ITIMER_PROF samples process CPU time instead.
enum Phase { BOOKSIM_PHASE, ALLREDUCE_PHASE, LINK_PHASE, OUTPUT_PHASE, PHASE_COUNT };

class PhaseProfile {
  static void Sample(int) {
    auto &profile = Instance();
    if(profile.sampling_) ++profile.samples_[profile.phase_];
  }
  volatile sig_atomic_t phase_ = BOOKSIM_PHASE;
  volatile sig_atomic_t samples_[PHASE_COUNT] = {};
  volatile sig_atomic_t sampling_ = 0;
  bool enabled_ = false;
  struct sigaction previous_handler_{};
  struct itimerval previous_timer_{};
  timespec cpu_start_{};
  double cpu_seconds_ = 0;
  int period_us_ = 0;
  static double Seconds(timespec const &value) {
    return double(value.tv_sec)+double(value.tv_nsec)*1e-9;
  }
public:
  static PhaseProfile &Instance() { static PhaseProfile profile; return profile; }
  bool Enabled() const { return enabled_; }
  sig_atomic_t Current() const { return phase_; }
  void Set(sig_atomic_t phase) { phase_ = phase; }
  void Start(int period_us) {
    if(enabled_ || period_us < 1000) Fail("Invalid CPU sampling period/state");
    for(int i = 0; i < PHASE_COUNT; ++i) samples_[i] = 0;
    phase_ = BOOKSIM_PHASE;
    struct sigaction action{};
    action.sa_handler = Sample;
    sigemptyset(&action.sa_mask);
    if(sigaction(SIGPROF, &action, &previous_handler_))
      Fail("Cannot install CPU sampler");
    struct itimerval timer{};
    timer.it_value.tv_sec = timer.it_interval.tv_sec = period_us/1000000;
    timer.it_value.tv_usec = timer.it_interval.tv_usec = period_us%1000000;
    period_us_ = period_us;
    enabled_ = true;
    sampling_ = 1;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &cpu_start_);
    if(setitimer(ITIMER_PROF, &timer, &previous_timer_)) {
      enabled_ = false;
      sampling_ = 0;
      sigaction(SIGPROF, &previous_handler_, nullptr);
      Fail("Cannot start CPU sampler");
    }
  }
  void Stop() {
    if(!enabled_) return;
    timespec end{};
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &end);
    struct itimerval stopped{};
    setitimer(ITIMER_PROF, &stopped, nullptr);
    sampling_ = 0;
    enabled_ = false;
    // Keep our harmless handler installed until any pending sample has been
    // delivered; it is also safe during process teardown. No other timer was
    // in use in this single-threaded standalone benchmark.
    cpu_seconds_ = Seconds(end)-Seconds(cpu_start_);
  }
  int PeriodUs() const { return period_us_; }
  double CpuSeconds() const { return cpu_seconds_; }
  int Samples(Phase phase) const { return samples_[phase]; }
  int Total() const {
    int count = 0;
    for(int i = 0; i < PHASE_COUNT; ++i) count += samples_[i];
    return count;
  }
  double Estimate(Phase phase) const {
    return Total() ? cpu_seconds_*Samples(phase)/Total() : 0;
  }
};

class PhaseScope {
  PhaseProfile &profile_;
  sig_atomic_t previous_;
  bool enabled_;
public:
  explicit PhaseScope(Phase phase) : profile_(PhaseProfile::Instance()),
      previous_(0), enabled_(profile_.Enabled()) {
    if(enabled_) { previous_ = profile_.Current(); profile_.Set(phase); }
  }
  ~PhaseScope() { if(enabled_) profile_.Set(previous_); }
  PhaseScope(PhaseScope const &) = delete;
  PhaseScope &operator=(PhaseScope const &) = delete;
};
}
#endif
