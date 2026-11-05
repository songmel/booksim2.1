#include "schedule.hpp"
namespace experiment {
Schedule Ring1D(int k, int bytes, int packet_bytes) {
  if(k < 2 || k > 8 || k%2 || bytes <= 0 || bytes%(k*k*packet_bytes))
    Fail<std::invalid_argument>("Ring1D requires an even mesh and packet-aligned rank chunks");
  Schedule plan("ring1d", k);
  const int ranks = k*k;
  plan.chunk_bytes.assign(ranks, bytes/ranks);
  std::vector<int> mapping;
  for(int x = 0; x < k; ++x) mapping.push_back(x);
  for(int y = 1; y < k; ++y) {
    if(y%2) for(int x = k-1; x >= 1; --x) mapping.push_back(x+k*y);
    else for(int x = 1; x < k; ++x) mapping.push_back(x+k*y);
  }
  for(int y = k-1; y >= 1; --y) mapping.push_back(k*y);
  std::vector<int> previous(ranks, -1);
  for(int step = 0; step < 2*(ranks-1); ++step) {
    std::vector<int> current(ranks);
    for(int rank = 0; rank < ranks; ++rank) {
      bool reduce = step < ranks-1;
      int chunk = reduce ? rank-step : rank+1-(step-(ranks-1));
      chunk = (chunk%ranks+ranks)%ranks;
      current[rank] = plan.Add(reduce ? REDUCE : COPY, mapping[rank],
                              mapping[(rank+1)%ranks], chunk, 1);
      plan.Depend(previous[rank], current[rank], true);
      plan.Depend(previous[(rank+ranks-1)%ranks], current[rank]);
    }
    previous.swap(current);
  }
  return plan;
}
}
