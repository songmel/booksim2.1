// $Id$

/*
 Copyright (c) 2007-2015, Trustees of The Leland Stanford Junior University
 All rights reserved.

 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that the following conditions are met:

 Redistributions of source code must retain the above copyright notice, this 
 list of conditions and the following disclaimer.
 Redistributions in binary form must reproduce the above copyright notice, this
 list of conditions and the following disclaimer in the documentation and/or
 other materials provided with the distribution.

 THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE 
 DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
 ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
 ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

#ifndef _CREDIT_HPP_
#define _CREDIT_HPP_

#include <set>
#include <stack>

// OPT
#include <vector>
#include <algorithm>
#include "implementation_opts.hpp"

// OPT4
#if OPT4
// Same sorted/unique iteration as std::set; keep its tiny storage across reuse.
class CreditVCSet {
  std::vector<int> _values;

public:
  typedef std::vector<int>::iterator iterator;
  typedef std::vector<int>::const_iterator const_iterator;

  CreditVCSet() { _values.reserve(4); }

  void insert(int vc) {
    iterator it = std::lower_bound(_values.begin(), _values.end(), vc);
    if(it == _values.end() || *it != vc) _values.insert(it, vc);
  }

  void clear() { _values.clear(); }
  bool empty() const { return _values.empty(); }
  size_t size() const { return _values.size(); }

  iterator begin() { return _values.begin(); }
  iterator end() { return _values.end(); }
  const_iterator begin() const { return _values.begin(); }
  const_iterator end() const { return _values.end(); }
};
#endif


class Credit {

public:

  // set<int> vc;
  // OPT4
#if OPT4
  typedef CreditVCSet VCSet;
#else
  typedef std::set<int> VCSet;
#endif
  VCSet vc;

  // these are only used by the event router
  bool head, tail;
  int  id;

  void Reset();
  
  static Credit * New();
  void Free();
  static void FreeAll();
  static int OutStanding();
private:

  static stack<Credit *> _all;
  static stack<Credit *> _free;

  Credit();
  ~Credit() {}

};

#endif
