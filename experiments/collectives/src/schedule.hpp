#ifndef EXPERIMENT_COLLECTIVE_SCHEDULE_HPP
#define EXPERIMENT_COLLECTIVE_SCHEDULE_HPP
#include <cstdint>
#include <stdexcept>
#include "experiment_error.hpp"
#include <string>
#include <vector>

namespace experiment {
enum Kind { REDUCE, COPY, BARRIER, EXCHANGE };
// Unique source/destination provenance for an AllToAll block. No reduction.
inline uint64_t ExchangeToken(int source, int destination) {
  return (uint64_t(source+1) << 32) | uint64_t(destination+1);
}
struct Operation {
  Kind kind;
  int source, destination, first_chunk, chunk_count, bytes, dependencies;
  std::vector<int> after_send, after_receive;
  Operation(Kind type, int src, int dst, int first, int count, int size)
    : kind(type), source(src), destination(dst), first_chunk(first),
      chunk_count(count), bytes(size), dependencies(0) {}
};
struct Schedule {
  std::string algorithm;
  int k, ranks, excluded;
  uint64_t contributors;
  std::vector<int> chunk_bytes;
  std::vector<Operation> operations;
  Schedule(std::string name, int width)
    : algorithm(name), k(width), ranks(width*width), excluded(-1),
      contributors(~uint64_t(0) >> (64-width*width)) {}
  int Add(Kind kind, int src, int dst, int first, int count) {
    int bytes = 0;
    for(int c = first; c < first+count; ++c) bytes += chunk_bytes.at(c);
    operations.emplace_back(kind, src, dst, first, count, bytes);
    return int(operations.size())-1;
  }
  void Depend(int before, int after, bool on_send = false) {
    if(before < 0) return;
    (on_send ? operations.at(before).after_send :
               operations.at(before).after_receive).push_back(after);
    ++operations.at(after).dependencies;
  }
};
Schedule Ring1D(int k, int bytes, int packet_bytes = 512);
Schedule Ring2D(int k, int bytes, int packet_bytes = 512);
Schedule TTO(int k, int bytes, int stripe_bytes, int packet_bytes = 512);
Schedule AllToAll(int k, int bytes, int packet_bytes = 512);
}
#endif
