# -*- coding:utf-8 -*-
"""
Author:
    Kai Zhang, kaizhangee@gmail.com
"""

import tensorflow as tf
from ..layer.common_layer import nffm
from ..utils.utize import cal_feature_number, get_linear_embedding, get_embedding, get_sequence_embedding


class Nffm(object):

    def __init__(self, feature_config_dict, embedding_size=3, l2_reg_linear=0.00001, dnn_hidden_units=(40, 20),
                 sequence_sprase_use_same_embedding=True, l2_reg_embedding=0.00001,
                 init_std=0.0001, seed=1024, task='binary'):
        # sequence_sprase_use_same_embedding, 通常sequence feature是sprase的子集

        self.feature_config_dict = feature_config_dict
        self.number_of_sprase_feature ,self.number_of_sequence_feature ,\
        self.number_of_dense_feature, self.sequence_feature_name = cal_feature_number(self.feature_config_dict)
        self.sprase_data = tf.placeholder(tf.int32, [None, self.number_of_sprase_feature])
        self.masked_sequence_data = tf.placeholder(tf.int32, [None, None])  # bs * T
        self.dense_data = tf.placeholder(tf.float32, [None, self.number_of_dense_feature])
        self.label = tf.placeholder(tf.float32, [None, ])
        self.lr = tf.placeholder(tf.float64, [])

        # Linear part
        sprase_feature, self.sprase_data_linear_embedding = \
            get_linear_embedding(self.feature_config_dict,self.sprase_data, self.number_of_sprase_feature)

        # sprase embedding
        self.embedding_dict, self.sprase_data_embedding = \
            get_embedding(sprase_feature, self.feature_config_dict,embedding_size, self.sprase_data)


        # nffm part
        sprase_data_list = tf.split(self.sprase_data, self.number_of_sprase_feature, axis=1)
        nffm_out = nffm(sprase_data_list, embedding_size, self.feature_config_dict)  # bs * (fs * (fs -1) / 2) * es
        nffm_out = tf.reduce_sum(nffm_out, axis=-1)  # bs * (fs * (fs -1) / 2)


        out = tf.concat([self.sprase_data_linear_embedding, nffm_out], axis=1)

        # sequence data
        if self.number_of_sequence_feature:
            self.sequence_data_embedding= get_sequence_embedding(
                self.embedding_dict, self.masked_sequence_data, self.sequence_feature_name, embedding_size)
            # FM use the average of the embedding directly.
            self.sequence_data_embedding = tf.reduce_mean(self.sequence_data_embedding, axis=1)  # bs * es
            out = tf.concat([out, self.sequence_data_embedding], axis=1)

        # Dense part
        if self.number_of_dense_feature:
            out = tf.concat([out, self.dense_data], axis=1)


        out = tf.concat([nffm_out, self.sprase_data_linear_embedding], axis=1)

        if self.number_of_sequence_feature:
            out = tf.concat([out, self.sequence_data_embedding], axis=1)

        if self.number_of_dense_feature:
            out = tf.concat([out, self.dense_data], axis=1)

        self.logits = tf.layers.dense(out, 1, activation=None,
                                      kernel_regularizer=tf.contrib.layers.l2_regularizer(l2_reg_linear))
        self.logits = tf.reshape(self.logits, [-1, ])

        self.pridict = tf.nn.sigmoid(self.logits)

        # Step variable
        self.global_step = tf.Variable(0, trainable=False, name='global_step')
        self.global_epoch_step = \
            tf.Variable(0, trainable=False, name='global_epoch_step')
        self.global_epoch_step_op = \
            tf.assign(self.global_epoch_step, self.global_epoch_step + 1)

        regulation_rate = 0.0
        self.loss = tf.reduce_mean(
            tf.nn.sigmoid_cross_entropy_with_logits(
                logits=self.logits,
                labels=self.label)
        )

        trainable_params = tf.trainable_variables()
        self.opt = tf.train.AdamOptimizer(learning_rate=self.lr)
        gradients = tf.gradients(self.loss, trainable_params)
        clip_gradients, _ = tf.clip_by_global_norm(gradients, 5)
        self.train_op = self.opt.apply_gradients(zip(clip_gradients, trainable_params), global_step=self.global_step)

    def train(self, sess, uij, l):
        if self.number_of_sequence_feature > 1:
            raise NotImplementedError
        loss, _ = sess.run([self.loss, self.train_op], feed_dict={
            self.sprase_data: uij[0],
            self.masked_sequence_data: uij[1][self.sequence_feature_name],
            self.dense_data: uij[2],
            self.label: uij[-1],
            self.lr: l,
        })
        return loss

    def _eval(self, sess, uij):
        if self.number_of_sequence_feature > 1:
            raise NotImplementedError
        pridict = sess.run(self.pridict, feed_dict={
            self.sprase_data: uij[0],
            self.masked_sequence_data: uij[1][self.sequence_feature_name],
            self.dense_data: uij[2],
        })
        return pridict

    def save(self, sess, path):
        saver = tf.train.Saver()
        saver.save(sess, save_path=path)

    def restore(self, sess, path):
        saver = tf.train.Saver()
        saver.restore(sess, save_path=path)

