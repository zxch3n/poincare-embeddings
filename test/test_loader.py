import data_loader
import numpy as np
import spacy
from time import time
import data
from word_vec_loader import WordVectorLoader
from torch.utils.data import Dataset, DataLoader


def clear():
    WordVectorLoader.word2index = None
    WordVectorLoader.index2word = None
    WordVectorLoader.embeddings = None
    WordVectorLoader.sense_num = None


nlp = spacy.load('en_core_web_lg')


def test_load_debug():
    clear()
    data_path = '../wordnet/debug.tsv'
    idx, objs, dwords = data.slurp(data_path, load_word=True, build_word_vector=True)
    _data = data_loader.WordsDataset(WordVectorLoader.word_vec, len(objs), WordVectorLoader.word_sim_adj)
    loader = DataLoader(
        _data,
        batch_size=2,
        shuffle=True,
        num_workers=2,
        collate_fn=data_loader.SNGraphDataset.collate,
        timeout=200
    )

    for i, (word, index) in enumerate(dwords.items()):
        a = np.array(nlp(word).vector)
        b = WordVectorLoader.word_vec[index - len(objs)]
        assert np.all(a == b), str(index - len(objs))

    for a, b in loader:
        print(a)
        print(b)

    print('a')


def test_load_mammals():
    clear()
    data_path = './wordnet/mammal_closure.tsv'
    start = time()
    idx, objs, dwords = data.slurp(data_path, load_word=True, build_word_vector=True)
    _data = data_loader.WordsDataset(WordVectorLoader.word_vec, len(objs), WordVectorLoader.word_sim_adj)
    used = time() - start
    print("Loading used time", used)
    loader = DataLoader(
        _data,
        batch_size=20,
        shuffle=True,
        num_workers=2,
        collate_fn=data_loader.SNGraphDataset.collate,
        timeout=20
    )

    for i, (word, index) in enumerate(dwords.items()):
        a = np.array(nlp(word).vector)
        b = WordVectorLoader.word_vec[index - len(objs)]
        assert np.all(a == b), str(index - len(objs)) + " " + word

    print("Average nn", _data.calc_word_average_adj())
    start = time()
    for a, b in loader:
        pass
    used = time() - start
    print(used)


def test_slurp_nouns():
    clear()
    data_path = '../wordnet/noun_closure.tsv'
    print(data_path)
    idx, objs, dwords = data.slurp(data_path, load_word=True, build_word_vector=True)


def test_load_nouns():
    clear()
    data_path = './wordnet/noun_closure.tsv'
    print(data_path)
    start = time()
    idx, objs, dwords = data.slurp(data_path, load_word=True, build_word_vector=True)
    print("loaded all words", time() - start)
    start = time()
    _data = data_loader.WordsDataset(WordVectorLoader.word_vec, len(objs), WordVectorLoader.word_sim_adj)
    print("init _data")
    print("loaded all adj", time() - start)
    loader = DataLoader(
        _data,
        batch_size=100,
        shuffle=True,
        num_workers=0,
        collate_fn=data_loader.SNGraphDataset.collate
    )
    print("start looping...")
    start = time()
    lasts = []
    last_start = start
    print(_data.calc_word_average_adj())
    for a, b in loader:
        last = time() - last_start
        last_start = time()
        lasts.append(last)
    used = time() - start
    print(_data.calc_word_average_adj())
    print("one epoch time", used)
    print("batch time", sum(lasts) / len(lasts))
