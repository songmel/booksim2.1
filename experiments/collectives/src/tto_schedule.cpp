#include "schedule.hpp"

#include <algorithm>
#include <cstdlib>

namespace experiment {
Schedule TTO(int k, int bytes, int stripe_bytes, int packet_bytes) {
  if(k < 2 || k > 8 || bytes <= 0 || bytes % packet_bytes ||
     stripe_bytes <= 0 || stripe_bytes % packet_bytes)
    Fail<std::invalid_argument>("TTO input and stripe sizes must be packet-aligned.");
  const int64_t wave_bytes = 3 * int64_t(stripe_bytes);
  const int waves = (int64_t(bytes) + wave_bytes - 1) / wave_bytes;
  Schedule plan("tto", k);
  int nodes = k*k;
  plan.excluded = k*(k-1);
  --plan.ranks;
  plan.contributors &= ~(uint64_t(1) << plan.excluded);
  // The author's generator rounds the number of three-tree waves up and
  // sends an equal, complete stripe on ALL three trees, including the last
  // wave. Bytes beyond the logical tensor are zero padding.
  plan.chunk_bytes.assign(3 * waves, stripe_bytes);

  // HPCA 2024 artifact, commit 156128c51d96bedebdc28315a974e8ae9fe2e502,
  // src/allreduce/mesh_allreduce_2d_overlap_1.py::build_trees.
  // Trees are directed outward for AllGather;
  // ReduceScatter reverses them. The excluded corner remains a relay in
  // trees 0 and 2, but contributes no gradient in any tree.
  const int roots[3] = {0, k-1, nodes-1};
  std::vector<std::vector<int> > parent(3, std::vector<int>(nodes, -1));
  std::vector<std::vector<bool> > used(nodes, std::vector<bool>(nodes, false));
  for(int node = 0; node < nodes; ++node) {
    int x = node%k, y = node/k;
    if(node != roots[0]) parent[0][node] = x ? node-1 : node-k;
    if(node != roots[2]) parent[2][node] = y != k-1 ? node+k : node+1;
    for(int tree = 0; tree < 3; tree += 2)
      if(parent[tree][node] >= 0) used[parent[tree][node]][node] = true;
  }
  // Use the reference's deterministic staircase construction. A generic
  // BFS may span the same ranks but picks different parents in larger meshes.
  int anchor = roots[1];
  for(int band = 0; band < k-1; ++band) {
    for(int node = anchor; node % k > 0; --node) parent[1][node-1] = node;
    for(int node = anchor; node / k < k-1; node += k) parent[1][node+k] = node;
    if(band < k-2) {
      int below_left = anchor - 1 + k;
      parent[1][below_left] = anchor - 1;
      anchor = below_left;
    }
  }
  for(int node = 0; node < nodes; ++node)
    if(node != plan.excluded && node != roots[1] && parent[1][node] < 0)
      Fail<std::logic_error>("TTO third tree does not span all contributing ranks.");
  for(int node = 0; node < nodes; ++node) {
    int p = parent[1][node];
    if(p < 0) continue;
    if(used[p][node]) Fail<std::logic_error>("TTO trees share a directed edge.");
    used[p][node] = true;
  }

  // One stripe per tree in each complete pipeline wave.
  std::vector<std::vector<int> > last_reduce(3, std::vector<int>(nodes, -1));
  std::vector<std::vector<int> > last_gather(3, std::vector<int>(nodes, -1));
  int barrier = plan.Add(BARRIER, -1, -1, 0, 0);
  for(int chunk = 0; chunk < int(plan.chunk_bytes.size()); ++chunk) {
    int tree = chunk % 3;
    std::vector<int> reduce(nodes, -1), gather(nodes, -1);
    for(int node = 0; node < nodes; ++node) {
      if(parent[tree][node] < 0) continue;
      reduce[node] = plan.Add(REDUCE, node, parent[tree][node], chunk, 1);
      gather[node] = plan.Add(COPY, parent[tree][node], node, chunk, 1);
      plan.Depend(reduce[node], barrier);
      plan.Depend(barrier, gather[node]);
      // hmc_fermat.py keeps this directed link occupied until message.end
      // arrives at the receiver. Injection completion alone is insufficient.
      plan.Depend(last_reduce[tree][node], reduce[node]);
      plan.Depend(last_gather[tree][node], gather[node]);
      last_reduce[tree][node] = reduce[node];
      last_gather[tree][node] = gather[node];
    }
    for(int node = 0; node < nodes; ++node) {
      int p = parent[tree][node];
      if(p < 0) continue;
      if(reduce[p] >= 0) plan.Depend(reduce[node], reduce[p]);
      if(gather[p] >= 0) plan.Depend(gather[p], gather[node]);
    }
  }
  return plan;
}
}
