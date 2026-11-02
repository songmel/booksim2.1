// Feature values are 0 or 1; unspecified features inherit OPT_ALL.
#ifndef BOOKSIM_IMPLEMENTATION_OPTS_HPP
#define BOOKSIM_IMPLEMENTATION_OPTS_HPP
#ifndef OPT_ALL
#define OPT_ALL 1
#endif
#ifndef OPT1
#define OPT1 OPT_ALL
#endif
#ifndef OPT2
#define OPT2 OPT_ALL
#endif
#ifndef OPT3
#define OPT3 OPT_ALL
#endif
#ifndef OPT4
#define OPT4 OPT_ALL
#endif
#ifndef OPT5
#define OPT5 OPT_ALL
#endif
#ifndef OPT6
#define OPT6 OPT_ALL
#endif
#define BOOKSIM_IMPLEMENTATION_MASK \
  ((OPT1 ? 1 : 0) | \
   (OPT2 ? 2 : 0) | \
   (OPT3 ? 4 : 0) | \
   (OPT4 ? 8 : 0) | \
   (OPT5 ? 16 : 0) | \
   (OPT6 ? 32 : 0))
#endif
