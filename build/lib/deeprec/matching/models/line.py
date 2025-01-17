"""
Author: Kai Zhang, kaizhangee@gmail.com
Ref:https://github.com/carpedm20/LINE
Reference: Tang J, Qu M, Wang M, et al. Line: Large-scale information network embedding[C]//Proceedings of the 24th International Conference on World Wide Web. International World Wide Web Conferences Steering Committee, 2015: 1067-1077.(https://arxiv.org/pdf/1503.03578.pdf)
"""
import os
import networkx as nx
import numpy as np
import sys
import time
import pickle
import tensorflow as tf
sys.path.append(os.path.abspath('.')[0:len(os.path.abspath('.'))-6] + "utilize")
from ..utilize.walker import Walker
from ..utilize.U import create_alias_table, alias_sample
from gensim.models import Word2Vec
import pandas as pd
import pdb
class AliasSampling:

    # Reference: https://en.wikipedia.org/wiki/Alias_method

    def __init__(self, prob):
        self.n = len(prob)
        self.U = np.array(prob) * self.n
        self.K = [i for i in range(len(prob))]
        overfull, underfull = [], []
        for i, U_i in enumerate(self.U):
            if U_i > 1:
                overfull.append(i)
            elif U_i < 1:
                underfull.append(i)
        while len(overfull) and len(underfull):
            i, j = overfull.pop(), underfull.pop()
            self.K[j] = i
            self.U[i] = self.U[i] - (1 - self.U[j])
            if self.U[i] > 1:
                overfull.append(i)
            elif self.U[i] < 1:
                underfull.append(i)

    def sampling(self, n=1):
        x = np.random.rand(n)
        i = np.floor(self.n * x)
        y = self.n * x - i
        i = i.astype(np.int32)
        res = [i[k] if y[k] < self.U[i[k]] else self.K[i[k]] for k in range(n)]
        if n == 1:
            return res[0]
        else:
            return res

class DBLPDataLoader:
    def __init__(self, graph):
        self.g = graph
        self.num_of_nodes = self.g.number_of_nodes()
        self.num_of_edges = self.g.number_of_edges()
        self.edges_raw = self.g.edges(data=True)
        self.nodes_raw = self.g.nodes(data=True)
        try:
            self.edge_distribution = np.array([attr['weight'] for _, _, attr in self.edges_raw], dtype=np.float32)
        except:
            self.edge_distribution = np.array([1]*self.num_of_edges, dtype=np.float32)
        self.edge_distribution /= np.sum(self.edge_distribution)
        self.edge_sampling = AliasSampling(prob=self.edge_distribution)

        try:
            tempnode = np.array([self.g.degree(node, weight='weight') for node, _ in self.nodes_raw], dtype=np.float32)
        except:
            tempnode = np.array([1]*self.num_of_nodes,dtype=np.float32)

        self.node_negative_distribution = np.power(tempnode, 0.75)
        self.node_negative_distribution /= np.sum(self.node_negative_distribution)
        self.node_sampling = AliasSampling(prob=self.node_negative_distribution)

        self.node_index = {}
        self.node_index_reversed = {}
        for index, (node, _) in enumerate(self.nodes_raw):
            self.node_index[node] = index
            self.node_index_reversed[index] = node
        self.edges = [(self.node_index[u], self.node_index[v]) for u, v, _ in self.edges_raw]

    def fetch_batch(self, batch_size=16, K=10, edge_sampling='atlas', node_sampling='atlas'):
        if edge_sampling == 'numpy':
            edge_batch_index = np.random.choice(self.num_of_edges, size=batch_size, p=self.edge_distribution)
        elif edge_sampling == 'atlas':
            edge_batch_index = self.edge_sampling.sampling(batch_size)
        elif edge_sampling == 'uniform':
            edge_batch_index = np.random.randint(0, self.num_of_edges, size=batch_size)
        u_i = []
        u_j = []
        label = []
        for edge_index in edge_batch_index:
            edge = self.edges[edge_index]
            if self.g.__class__ == nx.Graph:
                if np.random.rand() > 0.5:      # important: second-order proximity is for directed edge
                    edge = (edge[1], edge[0])
            u_i.append(edge[0])
            u_j.append(edge[1])
            label.append(1)
            for i in range(K):
                while True:
                    if node_sampling == 'numpy':
                        negative_node = np.random.choice(self.num_of_nodes, p=self.node_negative_distribution)
                    elif node_sampling == 'atlas':
                        negative_node = self.node_sampling.sampling()
                    elif node_sampling == 'uniform':
                        negative_node = np.random.randint(0, self.num_of_nodes)
                    if not self.g.has_edge(self.node_index_reversed[negative_node], self.node_index_reversed[edge[0]]):
                        break
                u_i.append(edge[0])
                u_j.append(negative_node)
                label.append(-1)
        return u_i, u_j, label

    def embedding_mapping(self, embedding):
        return {node: embedding[self.node_index[node]] for node, _ in self.nodes_raw}




class LINE:
    def __init__(self, graph, embedding_size=8, negative_ratio=5, learning_rate = 0.025, num_batches = 2000, order='second-order'):
        """

        :param graph:
        :param embedding_size:
        :param negative_ratio:
        :param order: 'first','second','all'
        """
        self.g = graph
        self.embedding_dim = embedding_size
        self.K = negative_ratio
        self.proximity = order
        self.learning_rate = learning_rate
        self.num_batches = num_batches
        self.final_embed = []


    def line_model(self, batch_size, num_of_nodes):
        self.u_i = tf.placeholder(name='u_i', dtype=tf.int32, shape=[batch_size * (self.K + 1)]) # K negtive, 1 postive
        self.u_j = tf.placeholder(name='u_j', dtype=tf.int32, shape=[batch_size * (self.K + 1)])
        self.label = tf.placeholder(name='label', dtype=tf.float32, shape=[batch_size * (self.K + 1)])
        self.embedding = tf.get_variable('target_embedding', [num_of_nodes, self.embedding_dim],
                                         initializer=tf.random_uniform_initializer(minval=-1., maxval=1.))
        self.u_i_embedding = tf.matmul(tf.one_hot(self.u_i, depth=num_of_nodes), self.embedding)
        if self.proximity == 'first-order':
            self.u_j_embedding = tf.matmul(tf.one_hot(self.u_j, depth=num_of_nodes), self.embedding)
        elif self.proximity == 'second-order':
            self.context_embedding = tf.get_variable('context_embedding', [num_of_nodes, self.embedding_dim],
                                                     initializer=tf.random_uniform_initializer(minval=-1., maxval=1.))
            self.u_j_embedding = tf.matmul(tf.one_hot(self.u_j, depth=num_of_nodes), self.context_embedding)

        self.inner_product = tf.reduce_sum(self.u_i_embedding * self.u_j_embedding, axis=1)
        self.loss = -tf.reduce_mean(tf.log_sigmoid(self.label * self.inner_product))
        # self.learning_rate = tf.placeholder(name='learning_rate', dtype=tf.float32)
        # self.optimizer = tf.train.GradientDescentOptimizer(learning_rate=self.learning_rate)
        self.optimizer = tf.train.RMSPropOptimizer(learning_rate=self.learning_rate)
        self.train_op = self.optimizer.minimize(self.loss)



    def train(self, batch_size=1024):
        data_loader = DBLPDataLoader(self.g)
        suffix = self.proximity
        num_of_nodes = data_loader.num_of_nodes
        #pdb.set_trace()
        model = self.line_model(batch_size, num_of_nodes)
        with tf.Session() as sess:
            tf.global_variables_initializer().run()
            initial_embedding = sess.run(self.embedding)
            sampling_time, training_time = 0, 0
            for b in range(self.num_batches):
                t1 = time.time()
                u_i, u_j, label = data_loader.fetch_batch(batch_size=batch_size, K=self.K)
                feed_dict = {self.u_i: u_i, self.u_j: u_j, self.label: label}
                t2 = time.time()
                sampling_time += t2 - t1
                if b % 100 != 0:
                    sess.run(self.train_op, feed_dict=feed_dict)
                    training_time += time.time() - t2
                    if self.learning_rate > self.learning_rate * 0.0001:
                        self.learning_rate = self.learning_rate * (1 - b / self.num_batches)
                    else:
                        self.learning_rate = self.learning_rate * 0.0001
                else:
                    loss = sess.run(self.loss, feed_dict=feed_dict)
                    print('%d\t%f\t%0.2f\t%0.2f\t%s' % (b, loss, sampling_time, training_time,
                                                        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())))
                    sampling_time, training_time = 0, 0
                if b % 1000 == 0 or b == (self.num_batches - 1):
                    embedding = sess.run(self.embedding)
                    normalized_embedding = embedding / np.linalg.norm(embedding, axis=1, keepdims=True)
                    #pickle.dump(data_loader.embedding_mapping(normalized_embedding),
                    #            open('data/embedding_%s.pkl' % suffix, 'wb'))
            self.final_embed = sess.run(self.embedding)
                    
    def get_embeddings(self):
        return self.final_embed



