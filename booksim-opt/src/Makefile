# $Id $

# Copyright (c) 2007-2015, Trustees of The Leland Stanford Junior University
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.
# Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

LEX = flex
YACC   = bison -y
DEFINE = 

OPT1 ?= 1
OPT2 ?= 1
OPT3 ?= 1
OPT4 ?= 1
OPT5 ?= 1
# OPT6: compact iSLIP storage, in-place request replacement, sparse clear.
OPT6 ?= 1

# Compiler optimizations are independent of the OPT1..OPT6 implementation paths.
# Plain make enables O3, host CPU instructions, LTO and exception-free C++.
# For a portable O3 build: make NATIVE=0 LTO=0 NO_EXCEPTIONS=0
OPTFLAGS ?= -O3 -g
NATIVE ?= 1
LTO ?= auto
NO_EXCEPTIONS ?= 1

# GCC PGO needs a training run between these builds, at the same source path:
#   make PGO=generate
#   ./booksim examples/mesh88_lat sim_type=batch batch_size=4 batch_count=1
#   make PGO=use
# Keep the same OPT switches/compiler settings in both stages. Use a fresh
# PGO_DIR for a new training session; clean deliberately preserves profile data.
PGO ?= off
PGO_DIR ?= $(CURDIR)/.pgo
PROFILE_DIR := $(abspath $(PGO_DIR))

COMPILER_FLAGS := $(OPTFLAGS)
ifeq ($(NATIVE),1)
COMPILER_FLAGS += -march=native
endif
ifneq ($(LTO),0)
COMPILER_FLAGS += -flto=$(LTO)
endif
ifeq ($(PGO),generate)
COMPILER_FLAGS += -fprofile-generate=$(PROFILE_DIR)
else ifeq ($(PGO),use)
# Do not silently accept missing or incompatible feedback as an optimized build.
COMPILER_FLAGS += -fprofile-use=$(PROFILE_DIR) -Werror=missing-profile -Werror=coverage-mismatch
else ifneq ($(PGO),off)
$(error PGO must be off, generate or use)
endif

CXX_OPT_FLAGS :=
ifeq ($(NO_EXCEPTIONS),1)
CXX_OPT_FLAGS += -fno-exceptions
endif

INCPATH = -I. -Iarbiters -Iallocators -Irouters -Inetworks -Ipower
CPPFLAGS += -Wall $(INCPATH) $(DEFINE)
CPPFLAGS += -DOPT1=$(OPT1)
CPPFLAGS += -DOPT2=$(OPT2)
CPPFLAGS += -DOPT3=$(OPT3)
CPPFLAGS += -DOPT4=$(OPT4)
CPPFLAGS += -DOPT5=$(OPT5)
CPPFLAGS += -DOPT6=$(OPT6)
LFLAGS +=

PROG := booksim
OPT_CONFIG := .opt_config

# simulator source files
CPP_SRCS = $(wildcard *.cpp) $(wildcard */*.cpp)
CPP_HDRS = $(wildcard *.hpp) $(wildcard */*.hpp)
CPP_DEPS = $(CPP_SRCS:.cpp=.d)
CPP_OBJS = $(CPP_SRCS:.cpp=.o)

LEX_SRCS = lex.yy.c
LEX_OBJS = lex.yy.o

YACC_SRCS = y.tab.c
YACC_HDRS = y.tab.h
YACC_OBJS = y.tab.o

OBJS :=  $(CPP_OBJS) $(LEX_OBJS) $(YACC_OBJS)

.PHONY: all clean distclean FORCE check-pgo

all: $(PROG)

# Track effective compiler/linker options as well as implementation switches.
# Change the timestamp only when settings or PGO feedback change. All objects
# depend on this stamp, so mode changes never require make clean.
$(OPT_CONFIG): FORCE | check-pgo
	@printf '%s\n' 'OPT1=$(OPT1)' 'OPT2=$(OPT2)' 'OPT3=$(OPT3)' 'OPT4=$(OPT4)' 'OPT5=$(OPT5)' 'OPT6=$(OPT6)' \
	  'CC=$(CC)' 'CXX=$(CXX)' 'LEX=$(LEX)' 'YACC=$(YACC)' \
	  'CPPFLAGS=$(CPPFLAGS)' 'CFLAGS=$(CFLAGS)' 'CXXFLAGS=$(CXXFLAGS)' \
	  'COMPILER_FLAGS=$(COMPILER_FLAGS)' 'CXX_OPT_FLAGS=$(CXX_OPT_FLAGS)' \
	  'LDFLAGS=$(LDFLAGS)' 'LFLAGS=$(LFLAGS)' 'LDLIBS=$(LDLIBS)' \
	  'PGO=$(PGO)' 'PGO_DIR=$(PROFILE_DIR)' > $@.tmp
ifeq ($(PGO),use)
	@find '$(PROFILE_DIR)' -type f -name '*.gcda' -exec sha256sum {} + | LC_ALL=C sort >> $@.tmp
endif
	@if cmp -s $@.tmp $@; then rm -f $@.tmp; else mv -f $@.tmp $@; fi

FORCE:

check-pgo:
ifeq ($(PGO),generate)
	@mkdir -p '$(PROFILE_DIR)'
else ifeq ($(PGO),use)
	@test -n "$(wildcard $(PROFILE_DIR)/*.gcda)" || { \
	  echo 'PGO=use requires training data in $(PROFILE_DIR); build PGO=generate and run booksim first.' >&2; exit 1; }
endif

$(PROG): $(OBJS)
	$(CXX) $(COMPILER_FLAGS) $(CXX_OPT_FLAGS) $(LDFLAGS) $(LFLAGS) $^ $(LDLIBS) -o $@

$(LEX_SRCS): config.l
	$(LEX) $<

# Grouped outputs prevent duplicate bison invocations during parallel builds.
$(YACC_SRCS) $(YACC_HDRS) &: config.y
	$(YACC) -d $<

$(LEX_OBJS): $(LEX_SRCS) $(YACC_HDRS)
	$(CC) $(CPPFLAGS) $(COMPILER_FLAGS) $(CFLAGS) -c $< -o $@

$(YACC_OBJS): $(YACC_SRCS)
	$(CC) $(CPPFLAGS) $(COMPILER_FLAGS) $(CFLAGS) -c $< -o $@

%.o: %.cpp
	$(CXX) $(CPPFLAGS) $(COMPILER_FLAGS) $(CXX_OPT_FLAGS) $(CXXFLAGS) -MMD -c $< -o $@

$(OBJS): $(OPT_CONFIG)

clean:
	rm -f $(YACC_SRCS) $(YACC_HDRS)
	rm -f $(LEX_SRCS)
	rm -f $(CPP_DEPS)
	rm -f $(OBJS)
	rm -f $(PROG)
	rm -f $(OPT_CONFIG) $(OPT_CONFIG).tmp

distclean: clean
	rm -f *~ */*~
	rm -f *.o */*.o
	rm -f *.d */*.d

-include $(CPP_DEPS)
