#!/usr/bin/env python3
# Copyright (c) 2017-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
#

from itertools import count
from collections import defaultdict as ddict
from nltk.corpus import wordnet as wn
from collections import Counter
import numpy as np
from word_vec_loader import WordVectorLoader
import torch as th
import spacy
import argparse
import random


def parse_seperator(line, length, sep='\t'):
    d = line.strip().split(sep)
    if len(d) == length:
        w = 1
    elif len(d) == length + 1:
        w = int(d[-1])
        d = d[:-1]
    else:
        raise RuntimeError(f'Malformed input ({line.strip()})')
    return tuple(d) + (w,)


def parse_tsv(line, length=2):
    return parse_seperator(line, length, '\t')


def parse_space(line, length=2):
    return parse_seperator(line, length, ' ')


def iter_line(fname, fparse, length=2, comment='#'):
    with open(fname, 'r') as fin:
        for line in fin:
            if line[0] == comment:
                continue
            tpl = fparse(line, length=length)
            if tpl is not None:
                yield tpl


def intmap_to_list(d):
    arr = [None for _ in range(len(d))]
    for v, i in d.items():
        arr[i] = v
    assert not any(x is None for x in arr)
    return arr


def load_all_related_words(idx, objects, enames):
    """

    :param idx: edges
    :param objects: index => name
    :param enames: name => index
    :return: word => index
    """

    def get_all_related_words():
        _words = set()
        for synset, index in enames.items():
            synset = wn.synset(synset)
            for lemma in synset.lemmas():
                name = lemma.name()
                if '_' in name:
                    continue
                _words.add(name)
        return _words

    def add_words_to_graph():
        for synset, index in enames.items():
            synset = wn.synset(synset)
            for lemma in synset.lemmas():
                name = lemma.name()
                if name in dwords:
                    idx.append((dwords[name], index, 1))

    dwords = {}
    word_vec = []
    nlp = spacy.load('en_core_web_lg')
    print('Loading wordnet words')
    all_words = get_all_related_words()
    for token in nlp(' '.join(all_words)):
        if token.text not in all_words:
            continue
        if token.text in dwords:
            continue
        vector = token.vector
        if np.sum(np.abs(vector)) < 1e-5:
            continue
        dwords[token.text] = len(word_vec) + len(objects)
        word_vec.append(vector)

    add_words_to_graph()
    print(f'Total {len(dwords)} words are added')
    word_vec = np.array(word_vec)
    return dwords, word_vec


def obj2map(objects):
    rt = {}
    for i, v in enumerate(objects):
        rt[v] = i
    return rt


def slurp(fin, fparse=parse_tsv, symmetrize=False, load_word=False, build_word_vector=False, objects=None):
    if objects is None:
        ecount = count()
        enames = ddict(ecount.__next__)
    else:
        enames = obj2map(objects)

    subs = []
    error_count = 0
    for i, j, w in iter_line(fin, fparse, length=2):
        if i == j:
            continue
        try:
            subs.append((enames[i], enames[j], w))
        except KeyError:
            error_count += 1

        if symmetrize:
            subs.append((enames[j], enames[i], w))

    if error_count:
        print(f"[ERROR]: Total {error_count} nodes weren't found in the train set")

    # freeze defaultdicts after training data and convert to arrays
    if objects is None:
        objects = intmap_to_list(dict(enames))
    dwords = None
    if load_word:
        dwords, word_vec = load_all_related_words(subs, objects, enames)
        if build_word_vector:
            WordVectorLoader.build(dwords, word_vec)
    idx = np.array(subs, dtype=np.int)
    print(f'slurp: objects={len(objects)}, edges={len(idx)}' + f', words={len(dwords)}' if load_word else '')
    return idx, objects, dwords


# randomly hold out links


def _get_root_and_leaf(idx):
    as_head = set()
    as_tail = set()
    max_ = 0
    for row in idx:
        h, t, _ = row
        as_head.add(int(h))
        as_tail.add(int(t))
        max_ = max(max_, h, t)
        
    u = set(range(max_))
    root_and_leaf = (u - as_head) | (u - as_tail)
    return root_and_leaf


SELDOM_THRESHOLD = 3


def _get_seldom_appear(idx):
    nodes = Counter()
    for row in idx:
        h, t, _ = [int(x) for x in row]
        nodes[h] += 1
        nodes[t] += 1

    return {x for x, c in nodes.items() if c < SELDOM_THRESHOLD}, dict(nodes)


def split_data(fin, fout, max_test_rate=0.05):
    idx, objs, _ = slurp(fin)
    root_and_leaf = _get_root_and_leaf(idx)
    seldom_appear, node_counts = _get_seldom_appear(idx)
    forbidden_node = root_and_leaf | seldom_appear
    train, test = [], []
    max_test_size = int(len(idx) * max_test_rate + 0.5)
    random.shuffle(idx)
    for h, t, _ in idx:
        t = int(t)
        h = int(h)
        if t not in forbidden_node \
                and h not in forbidden_node \
                and len(test) < max_test_size \
                and node_counts[h] >= SELDOM_THRESHOLD \
                and node_counts[t] >= SELDOM_THRESHOLD:
            node_counts[h] -= 1
            node_counts[t] -= 1
            test.append((objs[h], objs[t]))
        else:
            train.append((objs[h], objs[t]))

    print(f'TrainSet: {len(train)}')
    print(f'TestSet: {len(test)}')
    print(f'TestRate: {len(test) / (len(train) + len(test))}')
    with open(f'{fout}.train.tsv', 'w') as f:
        f.write('\n'.join(['\t'.join(x) for x in train]))
    with open(f'{fout}.test.tsv', 'w') as f:
        f.write('\n'.join(['\t'.join(x) for x in test]))
            
    return train, test


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Split Data')
    parser.add_argument('-input', help='Input data path', type=str, required=True)
    parser.add_argument('-output', help='Output data path', type=str, required=True)
    parser.add_argument('-test_rate', help='Test size rate', type=float, default=0.05)
    opt = parser.parse_args()
    split_data(opt.input, opt.output, opt.test_rate)
