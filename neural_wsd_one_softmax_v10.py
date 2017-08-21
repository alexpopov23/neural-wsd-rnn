import argparse
import random
import sys

import tensorflow as tf
import numpy as np

import data_ops_one_softmax
from gensim.models import KeyedVectors

from functools import partial
from sys import getsizeof


class Model:
    #TODO make model work with batches (no reason not to use them before the WSD part, I think)
    def __init__(self, is_first, synset2id, word_embedding_dim, vocab_size,
                 batch_size, seq_width, n_hidden, val_inputs, val_seq_lengths, val_flags, val_labels, val_indices):
        self.emb_placeholder = tf.placeholder(tf.float32, shape=[vocab_size, word_embedding_dim])
        self.embeddings = tf.Variable(self.emb_placeholder)
        self.set_embeddings = tf.assign(self.embeddings, self.emb_placeholder, validate_shape=False)
        # self.embeddings = tf.get_variable(name="W", shape=[len(src2id), word_embedding_dim], dtype=tf.float32,
        #                     initializer=tf.constant_initializer(word_embeddings), trainable=True)
        # weights and biases for the transorfmation after the RNN
        #TODO pick an initializer
        self.weights = tf.get_variable(name="softmax-w", shape=[2*n_hidden, len(synset2id)], dtype=tf.float32)
        self.biases = tf.get_variable(name="softmax-b", shape=[len(synset2id)], dtype=tf.float32)
        self.train_inputs = tf.placeholder(tf.int32, shape=[batch_size, seq_width])
        self.train_seq_lengths = tf.placeholder(tf.int32, shape=[batch_size])
        self.train_model_flags = tf.placeholder(tf.bool, shape=[batch_size, seq_width])
        self.train_labels = tf.placeholder(tf.int32, shape=[None, len(synset2id)])
        self.train_indices = tf.placeholder(tf.int32, shape=[None])
        self.val_inputs = tf.constant(val_inputs, tf.int32)
        self.val_seq_lengths = tf.constant(val_seq_lengths, tf.int32)
        self.val_flags = tf.constant(val_flags, tf.bool)
        self.val_labels = tf.constant(val_labels, tf.int32)
        self.val_indices = tf.constant(val_indices, tf.int32)

        reuse = None if is_first else True

        # create parameters for all word sense models
        with tf.variable_scope("word-sense-models") as scope:
            # Bidirectional recurrent neural network with LSTM cells
            initializer = tf.random_uniform_initializer(-1, 1)
            #with tf.variable_scope('forward'):
                # TODO: Use state_is_tuple=True
                # TODO: add dropout
            fw_cell = tf.contrib.rnn.LSTMCell(n_hidden, initializer=initializer)
            #with tf.variable_scope('backward'):
                # TODO: Use state_is_tuple=True
                # TODO: add dropout
            bw_cell = tf.contrib.rnn.LSTMCell(n_hidden, initializer=initializer)

            def biRNN_WSD (inputs, seq_lengths, indices, embeddings, weights, biases, labels):

                embedded_inputs = tf.nn.embedding_lookup(embeddings, inputs)
                #embedded_inputs = tf.unstack(tf.transpose(embedded_inputs, [1, 0, 2]))
                #embedded_inputs = tf.transpose(embedded_inputs, [1, 0, 2])

                # Get the blstm cell output
                rnn_outputs, _ = tf.nn.bidirectional_dynamic_rnn(fw_cell, bw_cell, embedded_inputs, dtype="float32",
                                                                 sequence_length=seq_lengths)
                rnn_outputs = tf.concat(rnn_outputs, 2)
                #rnn_outputs = tf.transpose(rnn_outputs, [1, 0, 2])

                scope.reuse_variables()

                rnn_outputs = tf.reshape(rnn_outputs, [-1, 2*n_hidden])
                target_outputs = tf.gather(rnn_outputs, indices)
                logits = tf.matmul(target_outputs, weights) + biases
                losses = tf.nn.softmax_cross_entropy_with_logits(logits=logits, labels=labels)
                cost = tf.reduce_mean(losses)

                return cost, logits

            self.loss, self.logits = biRNN_WSD(self.train_inputs, self.train_seq_lengths, self.train_indices,
                                               self.embeddings, self.weights, self.biases, self.train_labels)
            self.train_op = tf.train.GradientDescentOptimizer(learning_rate).minimize(self.loss)
            scope.reuse_variables()
            _, self.val_logits = biRNN_WSD(self.val_inputs, self.val_seq_lengths, self.val_indices,
                                           self.embeddings, self.weights, self.biases, self.val_labels)

def run_epoch(session, model, data, mode):

    inputs = data[0]
    seq_lengths = data[1]
    labels = data[2]
    words_to_disambiguate = data[3]
    indices = data[4]
    feed_dict = { model.train_inputs : inputs,
                  model.train_seq_lengths : seq_lengths,
                  model.train_model_flags : words_to_disambiguate,
                  model.train_indices : indices,
                  model.train_labels : labels}
    if mode == "train":
        ops = [model.train_op, model.loss, model.logits]
    elif mode == "val":
        ops = [model.train_op, model.loss, model.logits, model.val_logits]
    fetches = session.run(ops, feed_dict=feed_dict)

    return fetches


if __name__ == "__main__":

    parser = argparse.ArgumentParser(version='1.0',description='Train a neural POS tagger.')
    parser.add_argument('-word_embedding_method', dest='word_embedding_method', required=True, default="tensorflow",
                        help='Which method is used for loading the pretrained embeddings: tensorflow, gensim, glove?')
    parser.add_argument('-word_embedding_input', dest='word_embedding_input', required=False, default="wordform",
                        help='Are these embeddings of wordforms or lemmas (options are: wordform, lemma)?')
    parser.add_argument('-embeddings_load_script', dest='embeddings_load_script', required=False, default="None",
                        help='Path to the Python file that creates the word2vec object.')
    parser.add_argument('-word_embeddings_src_model', dest='word_embeddings_src_save_path', required=False,
                        help='The path to the pretrained model with the word embeddings (for the source language).')
    parser.add_argument('-word_embeddings_src_train_data', dest='word_embeddings_src_train_data', required=False,
                        help='The path to the corpus used for training the word embeddings for the source language.')
    parser.add_argument('-word_embedding_dim', dest='word_embedding_dim', required=False,
                        help='Size of the word embedding vectors.')
    parser.add_argument('-word_embedding_case', dest='word_embedding_case', required=False, default="lowercase",
                        help='Are the word embeddings trained on lowercased or mixedcased text?')
    parser.add_argument('-sense_embeddings', dest='sense_embeddings', required=False, default="None",
                        help='If a path to sense embeddings is passed to the script, label generation is done using them.')
    parser.add_argument('-learning_rate', dest='learning_rate', required=False, default=0.3,
                        help='How fast should the network learn.')
    parser.add_argument('-training_iterations', dest='training_iters', required=False, default=10000,
                        help='How many iterations should the network train for.')
    parser.add_argument('-batch_size', dest='batch_size', required=False, default=128,
                        help='Size of the training batches.')
    parser.add_argument('-n_hidden', dest='n_hidden', required=False, default=100,
                        help='Size of the hidden layer.')
    parser.add_argument('-sequence_width', dest='seq_width', required=False, default=50,
                        help='Maximum length of a sentence to be passed to the network (the rest is cut off).')
    parser.add_argument('-training_data', dest='training_data', required=True, default="brown",
                        help='The path to the gold corpus used for training/testing.')
    parser.add_argument('-test_data', dest='test_data', required=False, default="None",
                        help='The path to the gold corpus used for testing.')
    parser.add_argument('-lexicon', dest='lexicon', required=False, default="None",
                        help='The path to the location of the lexicon file.')
    parser.add_argument('-save_path', dest='save_path', required=False, default="None",
                        help='Path to where the model should be saved.')

    # read the parameters for the model and the data
    args = parser.parse_args()
    sense_embeddings = args.sense_embeddings
    embeddings_model_path = args.word_embeddings_src_save_path
    word_embedding_method = args.word_embedding_method
    word_embedding_dim = int(args.word_embedding_dim)
    word_embedding_case = args.word_embedding_case
    word_embedding_input = args.word_embedding_input
    word_embeddings = {}
    src2id = {}
    id2src = {}
    if word_embedding_method == "gensim":
        word_embeddings_model = KeyedVectors.load_word2vec_format(embeddings_model_path, binary=False)
        word_embeddings = word_embeddings_model.syn0
        id2src = word_embeddings_model.index2word
        for i, word in enumerate(id2src):
            src2id[word] = i
    elif word_embedding_method == "tensorflow":
        embeddings_load_script = args.embeddings_load_script
        sys.path.insert(0, embeddings_load_script)
        import word2vec_optimized as w2v
        word_embeddings = {} # store the normalized embeddings; keys are integers (0 to n)
        #TODO load the vectors from a saved structure, this TF graph below is pointless
        with tf.Graph().as_default(), tf.Session() as session:
            opts = w2v.Options()
            opts.train_data = args.word_embeddings_src_train_data
            opts.save_path = embeddings_model_path
            opts.emb_dim = word_embedding_dim
            model = w2v.Word2Vec(opts, session)
            ckpt = tf.train.get_checkpoint_state(args.word_embeddings_src_save_path)
            if ckpt and ckpt.model_checkpoint_path:
                model.saver.restore(session, ckpt.model_checkpoint_path)
            else:
                print("No valid checkpoint to reload a model was found!")
            src2id = model._word2id
            id2src = model._id2word
            word_embeddings = session.run(model._w_in)
            word_embeddings = tf.nn.l2_normalize(word_embeddings, 1).eval()
            #word_embeddings = np.vstack((word_embeddings, word_embedding_dim * [0.0]))
    elif word_embedding_method == "glove":
        word_embeddings, src2id, id2src = data_ops_one_softmax.loadGloveModel(embeddings_model_path)
        word_embeddings = np.asarray(word_embeddings)
        src2id["UNK"] = src2id["unk"]
        del src2id["unk"]
    if "UNK" not in src2id:
        unk = np.zeros(word_embedding_dim)
        src2id["UNK"] = len(src2id)
        word_embeddings = np.concatenate((word_embeddings, [unk]))

    # Network Parameters
    learning_rate = float(args.learning_rate) # Update rate for the weights
    training_iters = int(args.training_iters) # Number of training steps
    batch_size = int(args.batch_size) # Number of sentences passed to the network in one batch
    seq_width = int(args.seq_width) # Max sentence length (longer sentences are cut to this length)
    n_hidden = int(args.n_hidden) # Number of features/neurons in the hidden layer
    embedding_size = word_embedding_dim
    vocab_size = len(src2id)
    lexicon = args.lexicon

    data = args.training_data
    data, lemma2synsets, lemma2id, synset2id, id2synset = data_ops_one_softmax.read_folder_semcor(data)
    #random.shuffle(data)
    partition = int(len(data) * 0.95)
    train_data = data[:partition]
    test_data = args.test_data
    if test_data == "None":
        val_data = data[partition:]
    else:
        val_data, lemma2synsets, lemma2id, synset2id, id2synset = \
            data_ops_one_softmax.read_folder_semcor(test_data, lemma2synsets, lemma2id, synset2id, mode="test")
    val_inputs, val_seq_lengths, val_labels, val_words_to_disambiguate, \
    val_indices, val_lemmas_to_disambiguate, similarities = data_ops_one_softmax.format_data\
                                                    (val_data, src2id, lemma2synsets, synset2id, id2synset,
                                                    seq_width, word_embedding_case,
                                                    word_embedding_input, sense_embeddings)
    val_data = [val_inputs, val_seq_lengths, val_labels, val_words_to_disambiguate]

    # Function to calculate the accuracy on a batch of results and gold labels
    def accuracy(logits, labels, lemmas):

        matching_cases = 0
        eval_cases = 0
        for i, logit in enumerate(logits):
            pruned_logit = np.zeros([len(synset2id)])
            for synset in lemma2synsets[lemmas[i]]:
                id = synset2id[synset]
                pruned_logit[id] = logit[id]
            if np.argmax(pruned_logit) == np.argmax(labels[i]):
                matching_cases += 1
            eval_cases += 1

        return (100.0 * matching_cases) / eval_cases

    # Create a new batch from the training data (data, labels and sequence lengths)
    def new_batch (offset):

        batch = data[offset:(offset+batch_size)]
        inputs, seq_lengths, labels, words_to_disambiguate, indices, lemmas, _ = \
            data_ops_one_softmax.format_data(batch, src2id, lemma2synsets, synset2id, id2synset, seq_width,
                                             word_embedding_case, word_embedding_input, "None", similarities)
        return inputs, seq_lengths, labels, words_to_disambiguate, indices, lemmas

    model = Model(True, synset2id, word_embedding_dim, vocab_size, batch_size, seq_width,
                  n_hidden, val_inputs, val_seq_lengths, val_words_to_disambiguate, val_labels, val_indices)
    session = tf.Session()
    #session.run(tf.global_variables_initializer())
    init = tf.initialize_all_variables()
    session.run(init, feed_dict={model.emb_placeholder: word_embeddings})

    #session.run(model.set_embeddings, feed_dict={model.emb_placeholder: word_embeddings})

    batch_loss = 0
    for step in range(training_iters):
        offset = (step * batch_size) % (len(data) - batch_size)
        inputs, seq_lengths, labels, words_to_disambiguate, indices, lemmas_to_disambiguate = new_batch(offset)
        if (len(labels) == 0):
            continue
        input_data = [inputs, seq_lengths, labels, words_to_disambiguate, indices]

        if (step % 10 == 0):
            fetches = run_epoch(session, model, input_data, mode="val")
            print 'Minibatch accuracy: ' + str(accuracy(fetches[2], labels, lemmas_to_disambiguate))
            print 'Validation accuracy: ' + str(accuracy(fetches[3], val_labels, val_lemmas_to_disambiguate))
        else:
            fetches = run_epoch(session, model, input_data, mode="train")

        if (fetches[1] is not None):
            batch_loss += fetches[1]

        if (step % 100) == 0:
            print 'EPOCH: %d' % step
            print 'Averaged minibatch loss at step ' + str(step) + ': ' + str(batch_loss/100.0)
            batch_loss = 0.0
