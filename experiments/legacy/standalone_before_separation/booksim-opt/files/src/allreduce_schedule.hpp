#ifndef BOOKSIM_ALLREDUCE_SCHEDULE_HPP
#define BOOKSIM_ALLREDUCE_SCHEDULE_HPP

#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

// A dependency is released either when the last flit has been injected or
// when the complete message has arrived. No communication time is assumed here.
namespace allreduce {
enum Kind { REDUCE, COPY, BARRIER };
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
    : algorithm(name), k(width), ranks(width * width), excluded(-1),
      contributors(~uint64_t(0) >> (64 - width * width)) {}

  int Add(Kind kind, int src, int dst, int first, int count) {
    int bytes = 0;
    for(int c = first; c < first + count; ++c) bytes += chunk_bytes.at(c);
    operations.push_back(Operation(kind, src, dst, first, count, bytes));
    return int(operations.size()) - 1;
  }
  void Depend(int before, int after, bool on_send = false) {
    if(before < 0) return;
    (on_send ? operations.at(before).after_send :
               operations.at(before).after_receive).push_back(after);
    ++operations.at(after).dependencies;
  }
};

Schedule Ring2D(int k, int bytes);
Schedule TTO(int k, int bytes, int chunk_bytes);
}
#endif
