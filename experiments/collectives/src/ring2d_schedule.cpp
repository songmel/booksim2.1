#include "schedule.hpp"

namespace experiment {
Schedule Ring2D(int k, int bytes, int packet_bytes) {
  if(k < 2 || k > 8 || bytes <= 0 || bytes % (2 * k * k * packet_bytes))
    Fail<std::invalid_argument>("2D Ring requires packet-aligned elementary chunks.");
  Schedule plan("ring2d", k);
  plan.chunk_bytes.assign(2 * k * k, bytes / (2 * k * k));

  // Ring-2D in HPCA 2024, section II-B: two concurrent tensor halves.
  // Half A: RS(X), RS(Y), AG(Y), AG(X); half B exchanges X and Y.
  // Logical ring closing edges are routed through the physical mesh by DOR.
  for(int half = 0; half < 2; ++half) {
    std::vector<int> previous_receive(k*k, -1), previous_send(k*k, -1);
    for(int phase = 0; phase < 4; ++phase) {
      bool first_dimension = phase == 0 || phase == 3;
      bool x_dimension = first_dimension ? half == 0 : half != 0;
      bool reduce = phase < 2;
      std::vector<std::vector<int> > step(k-1, std::vector<int>(k*k));
      for(int round = 0; round < k-1; ++round) {
        for(int group = 0; group < k; ++group) {
          for(int position = 0; position < k; ++position) {
            int src = x_dimension ? position + k*group : group + k*position;
            int next = (position + 1) % k;
            int dst = x_dimension ? next + k*group : group + k*next;
            int shard = (position + (reduce ? 0 : 1) - round + k) % k;
            int first_shard = first_dimension ? shard : (group + 1) % k;
            int first = half*k*k + first_shard*k;
            int count = first_dimension ? k : 1;
            if(!first_dimension) first += shard;
            int id = plan.Add(reduce ? REDUCE : COPY, src, dst, first, count);
            step[round][src] = id;
            int prev = (position + k - 1) % k;
            int predecessor = x_dimension ? prev + k*group : group + k*prev;
            if(round) {
              plan.Depend(step[round-1][predecessor], id);
              plan.Depend(step[round-1][src], id, true);
            } else {
              plan.Depend(previous_receive[src], id);
              plan.Depend(previous_send[src], id, true);
            }
          }
        }
      }
      for(int node = 0; node < k*k; ++node) {
        int predecessor = x_dimension ? (node/k)*k + (node%k+k-1)%k :
                                        node%k + k*((node/k+k-1)%k);
        previous_receive[node] = step[k-2][predecessor];
        previous_send[node] = step[k-2][node];
      }
    }
  }
  return plan;
}
}
