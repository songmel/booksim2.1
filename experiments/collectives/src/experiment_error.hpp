#ifndef EXPERIMENT_ERROR_HPP
#define EXPERIMENT_ERROR_HPP

#include <cerrno>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <string>

namespace experiment {
// Keep every experiment-side validation in both compiler modes. A build with
// exceptions disabled reports the same error and exits instead of unwinding.
template<class Error = std::runtime_error>
[[noreturn]] inline void Fail(char const *message) {
#if defined(__EXCEPTIONS)
  throw Error(message);
#else
  std::fprintf(stderr, "EXPERIMENT_ERROR %s\n", message);
  std::exit(EXIT_FAILURE);
#endif
}

inline int ParseInt(std::string const &text) {
  char *end = nullptr;
  errno = 0;
  long value = std::strtol(text.c_str(), &end, 10);
  if(end == text.c_str() || *end != '\0')
    Fail<std::invalid_argument>("Invalid integer argument");
  if(errno == ERANGE || value < INT_MIN || value > INT_MAX)
    Fail<std::out_of_range>("Integer argument out of range");
  return int(value);
}
}
#endif
