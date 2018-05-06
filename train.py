#!/usr/bin/env python3
# Copyright (c) 2017-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
#

import numpy as np
import torch as th
from torch import nn
import time
import timeit
from torch.utils.data import DataLoader
import gc


if th.cuda.is_available():
    device = th.device('cuda')
else:
    device = th.device('cpu')


_lr_multiplier = 0.01


def train_mp(model, data, optimizer, opt, log, rank, queue, w_head=None, w_neg=None):
    try:
        train(model, data, optimizer, opt, log, rank, queue, w_head, w_neg)
    except Exception as err:
        log.exception(err)
        queue.put(None)


def train(model, data, optimizer, opt, log, rank=1, queue=None, w_head_data=None, w_neg_data=None):
    # setup parallel data loader
    loader = DataLoader(
        data,
        batch_size=opt.batchsize,
        shuffle=True,
        num_workers=opt.ndproc,
        collate_fn=data.collate,
        timeout=200
    )

    head_loader = []
    neg_loader = []
    if w_head_data is not None:
        head_loader = DataLoader(
            w_head_data,
            batch_size=opt.batchsize,
            shuffle=True,
            num_workers=opt.ndproc,
            collate_fn=w_head_data.collate,
            timeout=200
        )

    if w_neg_data is not None:
        neg_loader = DataLoader(
            w_neg_data,
            batch_size=opt.batchsize,
            shuffle=True,
            num_workers=opt.ndproc,
            collate_fn=w_neg_data.collate,
            timeout=200
        )

    for epoch in range(opt.epochs):
        epoch_loss = []
        loss = None
        data.burnin = False
        lr = opt.lr
        t_start = timeit.default_timer()
        if epoch < opt.burnin:
            data.burnin = True
            if opt.burnin <= 10:
                _lr_multiplier = 0.1
            else:
                _lr_multiplier = 0.01
            lr = opt.lr * _lr_multiplier
            if rank == 1:
                log.info(f'Burnin: lr={lr}')

        for _loader, params in ((loader, {}),
                                (head_loader, dict(fix_nn=False, embed_index=(1, 0, 1))),
                                (neg_loader, dict(fix_nn=False, embed_index=(1, 2, opt.negs)))):
            for inputs, targets in _loader:
                elapsed = timeit.default_timer() - t_start
                optimizer.zero_grad()
                preds = model(inputs.to(device), **params)
                loss = model.loss(preds, targets, size_average=True)
                loss.backward()
                optimizer.step(lr=lr)
                epoch_loss.append(loss.data.item())

        if rank == 1:
            emb = None
            if epoch == (opt.epochs - 1) or epoch % opt.eval_each == (opt.eval_each - 1):
                emb = model
            if queue is not None:
                queue.put(
                    (epoch, elapsed, np.mean(epoch_loss), emb, None)
                )
            else:
                log.info(
                    'info: {'
                    f'"elapsed": {elapsed}, '
                    f'"loss": {np.mean(epoch_loss)}, '
                    '}'
                )

        gc.collect()


class SingleThreadHandler:

    def __init__(self, log, train_types, test_types, data, fout, distfn, ranking):
        self.log = log
        self.types = train_types
        self.data = data
        self.fout = fout
        self.distfn = distfn
        self.ranking = ranking
        self.min_rank = (np.Inf, -1)
        self.max_map = (0, -1)

    def handle(self, msg):
        log = self.log
        types = self.types
        data = self.data
        fout = self.fout
        distfn = self.distfn
        ranking = self.ranking

        epoch, elapsed, loss, model, word_sim_loss = msg
        if model is not None:
            # save model to fout
            _fout = f'{fout}/{epoch}.nth'
            log.info(f'Saving model f{_fout}')
            th.save({
                'model': model.state_dict(),
                'epoch': epoch,
                'objects': data.objects,
            }, _fout)

            # compute embedding quality
            log.info('Computing ranking')
            _start_time = time.time()
            mrank, mAP = ranking(types, model, distfn)
            log.info(f'Computing finished. Used time: {time.time() - _start_time}')
            if mrank < self.min_rank[0]:
                self.min_rank = (mrank, epoch)
            if mAP > self.max_map[0]:
                self.max_map = (mAP, epoch)
            log.info(
                ('eval: {'
                 '"epoch": %d, '
                 '"elapsed": %.2f, '
                 '"loss": %.3f, '
                 f'"word_sim_loss": {word_sim_loss}, '
                 '"mean_rank": %.2f, '
                 '"mAP": %.4f, '
                 '"best_rank": %.2f, '
                 '"best_mAP": %.4f}') % (
                    epoch, elapsed, loss, mrank, mAP, self.min_rank[0], self.max_map[0])
            )
        else:
            log.info(f'json_log: {{"epoch": {epoch}, "loss": {loss}, "elapsed": {elapsed}}}')


def single_thread_train(model, data, optimizer, opt, log, handler, words_data=None,
                        w_head_data=None, w_neg_data=None):
    rank = 1
    model = model.to(device)
    loader = DataLoader(
        data,
        batch_size=opt.batchsize * 10,
        shuffle=True,
        num_workers=opt.ndproc,
        collate_fn=data.collate
    )

    if words_data is not None:
        words_loader = DataLoader(
            words_data,
            batch_size=1000,
            shuffle=True,
            num_workers=opt.ndproc,
            collate_fn=data.collate
        )
    else:
        words_loader = []

    loss_balance = 1.0
    if opt.cold:
        loss_balance *= 0.1
    for epoch in range(opt.epochs):
        epoch_loss = []
        epoch_words_loss = []
        loss = None
        data.burnin = False
        lr = opt.lr
        t_start = timeit.default_timer()
        if epoch < opt.burnin:
            data.burnin = True
            lr = opt.lr * 0.01
            if rank == 1:
                log.info(f'Burnin: lr={lr}')
        elif epoch == opt.burnin:
            loss_balance = 1.0

        node_iter = iter(loader)
        word_iter = iter(words_loader)
        i = 0
        alive = 3
        while alive:
            i = 1 - i
            if i == 0:
                v = next(node_iter, None)
                if v is None:
                    alive &= 1
                    continue
                inputs, targets = v
                optimizer.zero_grad()
                preds = model(inputs.to(device))
                loss = model.loss(preds, targets, size_average=True)
                loss.backward()
                optimizer.step(lr=lr)
                epoch_loss.append(loss.data.item())
            else:
                v = next(word_iter, None)
                if v is None:
                    alive &= 2
                    continue
                inputs, targets = v
                model.zero_grad_kb()
                optimizer.zero_grad()
                dists = model.calc_pair_sim(inputs.to(device), opt.mapping_func)
                loss = nn.MSELoss()(dists, targets.to(device))
                loss.backward()
                optimizer.step(lr=lr)
                epoch_words_loss.append(loss.data.item())

        elapsed = timeit.default_timer() - t_start
        if rank == 1:
            word_sim_loss = np.mean(epoch_words_loss) if len(epoch_words_loss) else None
            emb = None
            if epoch == (opt.epochs - 1) or epoch % opt.eval_each == (opt.eval_each - 1):
                emb = model
            handler.handle(
                (epoch, elapsed, np.mean(epoch_loss), emb, word_sim_loss)
            )
            log.info('info: {'
                     f'"k": {model.k.item()}'
                     '}')

        if not opt.nobalance:
            if epoch >= opt.burnin * opt.balance_stage:
                loss_balance *= np.mean(epoch_loss) / np.mean(epoch_words_loss)
                if rank == 1:
                    log.info(f'Loss balance: {loss_balance}')
        gc.collect()


