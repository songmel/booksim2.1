#include "schedule.hpp"

namespace experiment {
Schedule AllToAll(int k, int bytes, int packet_bytes) {
  if(k < 2 || k > 8 || packet_bytes <= 0 || bytes <= 0 || bytes%(k*k*packet_bytes))
    Fail<std::invalid_argument>("AllToAll requires packet-aligned equal blocks per destination");
  Schedule plan("alltoall", k);
  const int ranks = plan.ranks;
  // INPUT_BYTES is the entire send buffer per rank, including its local block.
  // EXCHANGE chunks denote receive slots indexed by source; destination names
  // the distinct input block being sent. Self blocks use a local copy, no link.
  plan.chunk_bytes.assign(ranks, bytes/ranks);
  // Direct exchange: all peer messages are initially eligible. The common
  // injector interleaves one packet per eligible message in round-robin order.
  // Offset ordering avoids concentrating all ranks' first packet on rank zero.
  for(int offset = 1; offset < ranks; ++offset)
    for(int source = 0; source < ranks; ++source)
      plan.Add(EXCHANGE, source, (source+offset)%ranks, source, 1);
  return plan;
}
}
