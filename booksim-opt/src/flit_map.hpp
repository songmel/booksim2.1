// OPT1: Allocation-free-after-growth tracking of live integer-ID flits.
#ifndef BOOKSIM_FLIT_MAP_HPP
#define BOOKSIM_FLIT_MAP_HPP
#include <vector>
#include <utility>
#include <cassert>
#include <cstddef>

class Flit;
class FlatFlitMap {
  typedef std::pair<int, Flit *> Value;
  std::vector<Value> _slots;
  size_t _size;
  size_t Index(int id) const { return static_cast<unsigned>(id)&(_slots.size()-1); }
  void Grow() {
    std::vector<Value> old;
    old.swap(_slots);
    _slots.assign(old.size()*2,Value(0,NULL));
    _size = 0;
    for(auto const &v : old) if(v.second) insert(v);
  }
public:
  class const_iterator {
    friend class FlatFlitMap;
    const FlatFlitMap *_map;
    size_t _index;
    const_iterator(const FlatFlitMap *map, size_t index) : _map(map), _index(index) {
      while(_index < _map->_slots.size() && !_map->_slots[_index].second) ++_index;
    }
  public:
    const_iterator() : _map(NULL), _index(0) {}
    const Value *operator->() const { return &_map->_slots[_index]; }
    const_iterator &operator++() {
      ++_index;
      while(_index < _map->_slots.size() && !_map->_slots[_index].second) ++_index;
      return *this;
    }
    const_iterator operator++(int) { auto old = *this; ++(*this); return old; }
    bool operator==(const_iterator const &o) const { return _map == o._map && _index == o._index; }
    bool operator!=(const_iterator const &o) const { return !(*this == o); }
  };
  typedef const_iterator iterator;
  FlatFlitMap() : _slots(128,Value(0,NULL)), _size(0) {}
  bool empty() const { return _size == 0; }
  size_t size() const { return _size; }
  const_iterator begin() const { return const_iterator(this,0); }
  const_iterator end() const { return const_iterator(this,_slots.size()); }
  const_iterator find(int id) const {
    size_t i = Index(id), mask = _slots.size()-1;
    while(_slots[i].second) {
      if(_slots[i].first == id) return const_iterator(this,i);
      i = (i+1)&mask;
    }
    return end();
  }
  size_t count(int id) const { return find(id) != end(); }
  void insert(Value const &v) {
    assert(v.second);
    if((_size+1)*2 > _slots.size()) Grow();
    size_t i = Index(v.first), mask = _slots.size()-1;
    while(_slots[i].second) {
      if(_slots[i].first == v.first) return;
      i = (i+1)&mask;
    }
    _slots[i] = v;
    ++_size;
  }
  void erase(const_iterator it) {
    assert(it._map == this && it._index < _slots.size());
    size_t hole = it._index, mask = _slots.size()-1;
    size_t scan = (hole+1)&mask;
    // Backward-shift deletion: no tombstones, no per-flit deallocation.
    while(_slots[scan].second) {
      size_t home = Index(_slots[scan].first);
      if(((hole-home)&mask) < ((scan-home)&mask)) {
        _slots[hole] = _slots[scan];
        hole = scan;
      }
      scan = (scan+1)&mask;
    }
    _slots[hole].second = NULL;
    --_size;
  }
  size_t erase(int id) {
    auto it = find(id);
    if(it == end()) return 0;
    erase(it);
    return 1;
  }
};
#endif
