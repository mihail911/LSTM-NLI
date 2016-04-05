#!/usr/bin/env python

import numpy as np
import sys
import theano
import theano.tensor as T

sys.path.append("/Users/mihaileric/Documents/Research/Lasagne")
import lasagne

from lasagne.layers import EmbeddingLayer, InputLayer, DenseLayer, ConcatLayer, get_output
from model.embeddings import EmbeddingTable
from util.stats import Stats
from util.utils import getMinibatchesIdx, convertLabelsToMat, generate_data


# Min/max sequence length
MIN_LENGTH = 50
MAX_LENGTH = 3
# Number of units in the hidden (recurrent) layer
N_HIDDEN = 100
# Number of training sequences in each batch
N_BATCH = 8
# Optimization learning rate
LEARNING_RATE = .001
# All gradients above this will be clipped
GRAD_CLIP = 100
# How often should we check the output?
EPOCH_SIZE = 100
# Number of epochs to train the net
NUM_EPOCHS = 100

NUM_DENSE_UNITS = 3


class SumEmbeddingLayer(lasagne.layers.Layer):

    def __init__(self, incoming):
        super(SumEmbeddingLayer, self).__init__(incoming)


    def get_output_shape_for(self, input_shape):
        """
        Return input shape for summed embeddings
        :param input_shape: Expected to be (batch_size, seq_length, embed_dim)
        :return: Expected to be (batch_size, embed_dim)
        """
        return (input_shape[0], input_shape[2])


    def get_output_for(self, input):
        """
        Sum embeddings for given sample across all time steps (seq_length)
        :param input:  Expected to be (batch_size, seq_length, embed_dim)
        :return: Summed embeddings of dim (batch_size, embed_dim)
        """
        return input.sum(axis=1)

trainData = "/Users/mihaileric/Documents/Research/LSTM-NLI/data/snli_1.0_train.jsonl"
trainDataStats = "/Users/mihaileric/Documents/Research/LSTM-NLI/data/train_dataStats.json"
trainLabels = "/Users/mihaileric/Documents/Research/LSTM-NLI/data/train_labels.json"
valData = "/Users/mihaileric/Documents/Research/LSTM-NLI/data/snli_1.0_dev.jsonl"
valDataStats = "/Users/mihaileric/Documents/Research/LSTM-NLI/data/dev_dataStats.json"
valLabels = "/Users/mihaileric/Documents/Research/LSTM-NLI/data/dev_labels.json"


def form_mask_input(num_samples, seq_len, max_seq_len, pad_dir):
    mask = np.zeros((num_samples, max_seq_len)).astype(np.float32)
    if pad_dir == 'right':
        mask[:, 0:seq_len] = 1.
    elif pad_dir == 'left':
        mask[:, -seq_len:] = 1.

    return mask


def convert_idx_to_label(idx_array):
    labels = ["entailment", "neutral", "contradiction"]
    predictions = []
    for entry in idx_array:
        predictions.append(labels[entry])

    return predictions


def main(num_epochs=NUM_EPOCHS):
    # Set random seed for deterministic results
    np.random.seed(0)
    num_ex_to_train = -1

    # Load embedding table
    embedData = "/Users/mihaileric/Documents/Research/LSTM-NLI/data/glove.6B.50d.txt.gz"
    table = EmbeddingTable(embedData)
    vocab_size = table.sizeVocab
    dim_embeddings = table.dimEmbeddings
    embeddings_mat = table.embeddings


    #train_prem, train_hyp = generate_data(trainData, trainDataStats, "right", table)
    val_prem, val_hyp = generate_data(valData, valDataStats, "right", table, seq_len=18)
    #train_labels = convertLabelsToMat(trainData)
    val_labels = convertLabelsToMat(valData)

    # Want to test for overfitting capabilities of model
    if num_ex_to_train > 0:
        val_prem = val_prem[0:num_ex_to_train]
        val_hyp = val_hyp[0:num_ex_to_train]
        val_labels = val_labels[0:num_ex_to_train]

    # Theano expressions for premise/hypothesis inputs to network
    x_p = T.imatrix()
    x_h = T.imatrix()
    target_values = T.fmatrix(name="target_output")

    # Test points
    x_prem = np.array([[0, 2], [0, 6]]).astype(np.int32)
    x_hyp = np.array([[1, 13], [ 400001, 400001]]).astype(np.int32)
    #x_target = np.array([0, 2, 2]).astype(np.float32)
    x_target = np.array([[1, 0, 0], [1, 0, 0]]).astype(np.float32)

    # Embedding layer for premise
    l_in_prem = InputLayer((N_BATCH, MAX_LENGTH))
    l_embed_prem = EmbeddingLayer(l_in_prem, input_size=vocab_size,
                        output_size=dim_embeddings, W=embeddings_mat)

    # Embedding layer for hypothesis
    l_in_hyp = InputLayer((N_BATCH, MAX_LENGTH))
    l_embed_hyp = EmbeddingLayer(l_in_hyp, input_size=vocab_size,
                        output_size=dim_embeddings, W=embeddings_mat)


    # Ensure embedding matrix parameters are not trainable
    l_embed_hyp.params[l_embed_hyp.W].remove('trainable')
    l_embed_prem.params[l_embed_prem.W].remove('trainable')

    l_embed_hyp_sum = SumEmbeddingLayer(l_embed_hyp)
    l_embed_prem_sum = SumEmbeddingLayer(l_embed_prem)


    l_concat = ConcatLayer([l_embed_hyp_sum, l_embed_prem_sum])

    # Note dense layer uses ReLu nonlinearity by default
    l_dense = DenseLayer(l_concat, num_units=NUM_DENSE_UNITS, nonlinearity=lasagne.nonlinearities.softmax)
    # Note this is the output of the network
    network_output = get_output(l_dense, {l_in_prem: x_p, l_in_hyp: x_h}) # Will have shape (batch_size, 3)
    f_dense_output = theano.function([x_p, x_h], network_output, on_unused_input='warn')

    cost = T.nnet.categorical_crossentropy(network_output, target_values).mean()
    compute_cost = theano.function([x_p, x_h, target_values], cost)

    accuracy = T.mean(T.eq(T.argmax(network_output, axis=-1), T.argmax(target_values, axis=-1)))
    compute_accuracy = theano.function([x_p, x_h, target_values], accuracy)

    label_output = T.argmax(network_output, axis=-1)
    predict = theano.function([x_p, x_h], label_output)

    # Define update/train functions
    all_params = lasagne.layers.get_all_params(l_dense, trainable=True)
    updates = lasagne.updates.rmsprop(cost, all_params, LEARNING_RATE)
    train = theano.function([x_p, x_h, target_values], cost, updates=updates)

    # TODO: Test that training is working appropriately
    # TODO: Augment embedding layer to allow for masking inputs

    exp_name = "/Users/mihaileric/Documents/Research/LSTM-NLI/log/sum_embeddings.log"
    stats = Stats(exp_name)
    acc_num = 10

    minibatches = getMinibatchesIdx(val_prem.shape[0], N_BATCH)
    print("Training ...")
    try:
        total_num_ex = 0
        for epoch in xrange(num_epochs):
            for _, minibatch in minibatches:
                total_num_ex += len(minibatch)
                stats.log("Processed {0} total examples in epoch {1}".format(str(total_num_ex),
                                                                             str(epoch)))
                prem_batch = val_prem[minibatch]
                hyp_batch = val_hyp[minibatch]
                labels_batch = val_labels[minibatch]
                train(prem_batch, hyp_batch, labels_batch)
                cost_val = compute_cost(prem_batch, hyp_batch, labels_batch)

                stats.recordCost(total_num_ex, cost_val)
                # Periodically compute and log accuracy
                if total_num_ex%(acc_num*N_BATCH) == 0:
                    dev_acc = compute_accuracy(val_prem, val_hyp, val_labels)
                    stats.recordAcc(total_num_ex, dev_acc, dataset="dev")

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
