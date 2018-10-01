# Copyright 2017 Neural Networks and Deep Learning lab, MIPT
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from keras.layers import Input, LSTM, Lambda, Dense, Dropout
from keras.models import Model
from keras.layers.wrappers import Bidirectional
from keras.initializers import glorot_uniform, Orthogonal
from keras import backend as K

from deeppavlov.core.common.log import get_logger
from deeppavlov.core.common.registry import register
from deeppavlov.models.ranking.bilstm_siamese_network import BiLSTMSiameseNetwork
from deeppavlov.core.layers.keras_layers import FullMatchingLayer, MaxpoolingMatchingLayer
from deeppavlov.core.layers.keras_layers import AttentiveMatchingLayer, MaxattentiveMatchingLayer

log = get_logger(__name__)


@register('mpm_nn')
class MPMNetwork(BiLSTMSiameseNetwork):

    """The class implementing a siamese neural network with BiLSTM and max pooling.

    There is a possibility to use a binary cross-entropy loss as well as
    a triplet loss with random or hard negative sampling.

    Args:
        len_vocab: A size of the vocabulary to build embedding layer.
        seed: Random seed.
        embedding_dim: Dimensionality of token (word) embeddings.
            ``hidden_dim`` should be doubled to get the actual dimensionality.
            If ``False``, the last hidden state of the RNN will be used.
        triplet_loss: Whether to use a model with triplet loss.
            If ``False``, a model with crossentropy loss will be used.
        margin: A margin parameter for triplet loss. Only required if ``triplet_loss`` is set to ``True``.
        hard_triplets: Whether to use hard triplets sampling to train the model
            i.e. to choose negative samples close to positive ones.
            If set to ``False`` random sampling will be used.
            Only required if ``triplet_loss`` is set to ``True``.
    """

    def __init__(self,
                 dense_dim: int = 50,
                 perspective_num: int = 20,
                 aggregation_dim: int = 200,
                 ldrop_val: float = 0.0,
                 recdrop_val: float = 0.0,
                 inpdrop_val: float = 0.0,
                 dropout_val: float = 0.0,
                 seed: int = None,
                 *args,
                 **kwargs):

        self.dense_dim = dense_dim
        self.perspective_num = perspective_num
        self.aggregation_dim = aggregation_dim
        self.ldrop_val = ldrop_val
        self.recdrop_val = recdrop_val
        self.inpdrop_val = inpdrop_val
        self.dropout_val = dropout_val
        self.seed = seed
        self.triplet_mode = kwargs.get("triplet_loss")

        super(MPMNetwork, self).__init__(*args, **kwargs)

    def create_lstm_layer_1(self):
        ker_in = glorot_uniform(seed=self.seed)
        rec_in = Orthogonal(seed=self.seed)
        bioutp = Bidirectional(LSTM(self.hidden_dim,
                                    input_shape=(self.max_sequence_length, self.embedding_dim,),
                                    kernel_regularizer=None,
                                    recurrent_regularizer=None,
                                    bias_regularizer=None,
                                    activity_regularizer=None,
                                    recurrent_dropout=self.recdrop_val,
                                    dropout=self.inpdrop_val,
                                    kernel_initializer=ker_in,
                                    recurrent_initializer=rec_in,
                                    return_sequences=True), merge_mode=None)
        return bioutp

    def create_lstm_layer_2(self):
        ker_in = glorot_uniform(seed=self.seed)
        rec_in = Orthogonal(seed=self.seed)
        bioutp = Bidirectional(LSTM(self.aggregation_dim,
                                    input_shape=(self.max_sequence_length, 8*self.perspective_num,),
                                    kernel_regularizer=None,
                                    recurrent_regularizer=None,
                                    bias_regularizer=None,
                                    activity_regularizer=None,
                                    recurrent_dropout=self.recdrop_val,
                                    dropout=self.inpdrop_val,
                                    kernel_initializer=ker_in,
                                    recurrent_initializer=rec_in,
                                    return_sequences=False),
                               merge_mode='concat',
                               name="sentence_embedding")
        return bioutp

    def create_model(self) -> Model:
        if self.use_matrix:
            context = Input(shape=(self.max_sequence_length,))
            response = Input(shape=(self.max_sequence_length,))
            emb_layer = self.embedding_layer()
            emb_c = emb_layer(context)
            emb_r = emb_layer(response)
        else:
            context = Input(shape=(self.max_sequence_length, self.embedding_dim,))
            response = Input(shape=(self.max_sequence_length, self.embedding_dim,))
            emb_c = context
            emb_r = response
        lstm_layer = self.create_lstm_layer_1()
        lstm_a = lstm_layer(emb_c)
        lstm_b = lstm_layer(emb_r)

        f_layer_f = FullMatchingLayer(self.perspective_num)
        f_layer_b = FullMatchingLayer(self.perspective_num)
        f_a_forw = f_layer_f([lstm_a[0], lstm_b[0]])[0]
        f_a_back = f_layer_b([Lambda(lambda x: K.reverse(x, 1))(lstm_a[1]),
                                      Lambda(lambda x: K.reverse(x, 1))(lstm_b[1])])[0]
        f_a_back = Lambda(lambda x: K.reverse(x, 1))(f_a_back)
        f_b_forw = f_layer_f([lstm_b[0], lstm_a[0]])[0]
        f_b_back = f_layer_b([Lambda(lambda x: K.reverse(x, 1))(lstm_b[1]),
                                      Lambda(lambda x: K.reverse(x, 1))(lstm_a[1])])[0]
        f_b_back = Lambda(lambda x: K.reverse(x, 1))(f_b_back)

        mp_layer_f = MaxpoolingMatchingLayer(self.perspective_num)
        mp_layer_b = MaxpoolingMatchingLayer(self.perspective_num)
        mp_a_forw = mp_layer_f([lstm_a[0], lstm_b[0]])[0]
        mp_a_back = mp_layer_b([lstm_a[1], lstm_b[1]])[0]
        mp_b_forw = mp_layer_f([lstm_b[0], lstm_a[0]])[0]
        mp_b_back = mp_layer_b([lstm_b[1], lstm_a[1]])[0]

        at_layer_f = AttentiveMatchingLayer(self.perspective_num)
        at_layer_b = AttentiveMatchingLayer(self.perspective_num)
        at_a_forw = at_layer_f([lstm_a[0], lstm_b[0]])[0]
        at_a_back = at_layer_b([lstm_a[1], lstm_b[1]])[0]
        at_b_forw = at_layer_f([lstm_b[0], lstm_a[0]])[0]
        at_b_back = at_layer_b([lstm_b[1], lstm_a[1]])[0]

        ma_layer_f = MaxattentiveMatchingLayer(self.perspective_num)
        ma_layer_b = MaxattentiveMatchingLayer(self.perspective_num)
        ma_a_forw = ma_layer_f([lstm_a[0], lstm_b[0]])[0]
        ma_a_back = ma_layer_b([lstm_a[1], lstm_b[1]])[0]
        ma_b_forw = ma_layer_f([lstm_b[0], lstm_a[0]])[0]
        ma_b_back = ma_layer_b([lstm_b[1], lstm_a[1]])[0]

        concat_a = Lambda(lambda x: K.concatenate(x, axis=-1))([f_a_forw, f_a_back,
                                                                mp_a_forw, mp_a_back,
                                                                at_a_forw, at_a_back,
                                                                ma_a_forw, ma_a_back])
        concat_b = Lambda(lambda x: K.concatenate(x, axis=-1))([f_b_forw, f_b_back,
                                                                mp_b_forw, mp_b_back,
                                                                at_b_forw, at_b_back,
                                                                ma_b_forw, ma_b_back])

        concat_a = Dropout(self.ldrop_val)(concat_a)
        concat_b = Dropout(self.ldrop_val)(concat_b)

        lstm_layer_agg = self.create_lstm_layer_2()
        agg_a = lstm_layer_agg(concat_a)
        agg_b = lstm_layer_agg(concat_b)

        agg_a = Dropout(self.dropout_val)(agg_a)
        agg_b = Dropout(self.dropout_val)(agg_b)

        reduced = Lambda(lambda x: K.concatenate(x, axis=-1))([agg_a, agg_b])

        if self.triplet_mode:
            dist = Lambda(self._pairwise_distances)([agg_a, agg_b])
        else:
            ker_in = glorot_uniform(seed=self.seed)
            dense = Dense(self.dense_dim, kernel_initializer=ker_in)(reduced)
            dist = Dense(1, activation='sigmoid', name="score_model")(dense)
        model = Model([context, response], dist)
        return model