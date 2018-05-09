from keras import backend as bknd
from keras.callbacks import *
from keras.layers import *
from keras.models import *
from keras.optimizers import SGD
from keras.utils import *
from keras.callbacks import ModelCheckpoint
from keras.callbacks import TensorBoard

from STN.spatial_transformer import SpatialTransformer
from batch_generator import width, height, characters, label_len, label_classes
from batch_generator import img_gen, img_gen_val

learning_rate = 0.002
inputShape = Input((width, height, 3))  # base on Tensorflow backend


# functions
class Evaluate(Callback):

    def on_epoch_end(self, epoch, logs=None):
        acc = evaluate(base_model)
        print('')
        print('acc:'+str(acc)+"%")


evaluator = Evaluate()


def evaluate(input_model):
    correct_prediction = 0
    generator = img_gen_val()

    x_test, y_test = next(generator)
    # print(" ")
    y_pred = input_model.predict(x_test)  # y_pred.shape is (100, 25, 12)
    shape = y_pred[:, 2:, :].shape  # (100, 23, 12)
    ctc_decode = bknd.ctc_decode(y_pred[:, 2:, :], input_length=np.ones(shape[0])*shape[1])[0][0]
    out = bknd.get_value(ctc_decode)[:, :label_len]

    for m in range(1000):
        result_str = ''.join([characters[k] for k in out[m]])
        result_str = result_str.replace('-', '')
        if result_str == y_test[m]:
            correct_prediction += 1
            # print(m)
        else:
            print(result_str, y_test[m])
            
    return correct_prediction*1.0/10


def ctc_lambda_func(args):
    iy_pred, ilabels, iinput_length, ilabel_length = args
    # the 2 is critical here since the first couple outputs of the RNN
    # tend to be garbage:
    iy_pred = iy_pred[:, 2:, :] 
    return bknd.ctc_batch_cost(ilabels, iy_pred, iinput_length, ilabel_length)


# initial bias_initializer
def loc_net(input_shape):
    b = np.zeros((2, 3), dtype='float32')
    b[0, 0] = 1
    b[1, 1] = 1
    w = np.zeros((64, 6), dtype='float32')
    weights = [w, b.flatten()]

    loc_input = Input(input_shape)

    loc_conv_1 = Conv2D(16, (5, 5), padding='same', activation='relu')(loc_input)
    loc_conv_2 = Conv2D(32, (5, 5), padding='same', activation='relu')(loc_conv_1)
    loc_fla = Flatten()(loc_conv_2)
    loc_fc_1 = Dense(64, activation='relu')(loc_fla)
    loc_fc_2 = Dense(6, weights=weights)(loc_fc_1)

    output = Model(inputs=loc_input, outputs=loc_fc_2)

    return output


# build model

'''----------------------STN-------------------------'''
stn_input_shape = inputShape.get_shape()
loc_input_shape = (stn_input_shape[1].value, stn_input_shape[2].value, stn_input_shape[3].value)
stn = SpatialTransformer(localization_net=loc_net(loc_input_shape),
                         output_size=(loc_input_shape[0], loc_input_shape[1]))(inputShape)


conv_1 = Conv2D(64, (3, 3), activation='relu', padding='same')(stn)
batchnorm_1 = BatchNormalization()(conv_1)
# pool_1 = MaxPooling2D(pool_size=(2, 2))(batchnorm_1)

conv_2 = Conv2D(128, (3, 3), activation='relu', padding='same')(conv_1)
conv_3 = Conv2D(256, (3, 3), activation='relu', padding='same')(conv_2)
batchnorm_3 = BatchNormalization()(conv_3)
pool_3 = MaxPooling2D(pool_size=(2, 2))(batchnorm_3)

conv_4 = Conv2D(256, (3, 3), activation='relu', padding='same')(pool_3)
conv_5 = Conv2D(512, (3, 3), activation='relu', padding='same')(conv_4)
batchnorm_5 = BatchNormalization()(conv_5)
pool_5 = MaxPooling2D(pool_size=(2, 2))(batchnorm_5)

conv_6 = Conv2D(512, (3, 3), activation='relu', padding='same')(pool_5)
conv_7 = Conv2D(512, (3, 3), activation='relu', padding='same')(conv_6)
batchnorm_7 = BatchNormalization()(conv_7)

bn_shape = batchnorm_7.get_shape()  # (?, {dimension}50, {dimension}12, {dimension}256)
print(bn_shape)  # (?, 50, 7, 512)

x_reshape = Reshape(target_shape=(int(bn_shape[1]), int(bn_shape[2] * bn_shape[3])))(batchnorm_7)

fc_1 = Dense(128, activation='relu')(x_reshape)  # (?, 50, 128)

print(x_reshape.get_shape())  # (?, 50, 3584)
print(fc_1.get_shape())  # (?, 50, 128)

rnn_1 = LSTM(128, name="rnn1", kernel_initializer="he_normal", return_sequences=True)(fc_1)
rnn_1b = LSTM(128, name="rnn1_b", kernel_initializer="he_normal",
              go_backwards=True, return_sequences=True)(fc_1)
rnn1_merged = add([rnn_1, rnn_1b])

rnn_2 = LSTM(128, name="rnn2", kernel_initializer="he_normal", return_sequences=True)(rnn1_merged)
rnn_2b = LSTM(128, name="rnn2_b", kernel_initializer="he_normal",
              go_backwards=True, return_sequences=True)(rnn1_merged)
rnn2_merged = concatenate([rnn_2, rnn_2b])

drop_1 = Dropout(0.25)(rnn2_merged)

fc_2 = Dense(label_classes, kernel_initializer='he_normal', activation='softmax')(drop_1)

# model setting
base_model = Model(inputs=inputShape, outputs=fc_2)

labels = Input(name='the_labels', shape=[label_len], dtype='float32')
input_length = Input(name='input_length', shape=[1], dtype='int64')
label_length = Input(name='label_length', shape=[1], dtype='int64')

loss_out = Lambda(ctc_lambda_func, output_shape=(1,), name='ctc')([fc_2, labels, input_length, label_length])

model = Model(inputs=[inputShape, labels, input_length, label_length], outputs=[loss_out])

# clipnorm seems to speeds up convergence
sgd = SGD(lr=learning_rate, decay=1e-6, momentum=0.9, nesterov=True, clipnorm=5)
adam = optimizers.Adam()

model.compile(loss={'ctc': lambda y_true, y_pred: y_pred}, optimizer=sgd)
# model.compile(loss={'ctc': lambda y_true, y_pred: y_pred}, optimizer=adam)
# model.compile(loss={'ctc': lambda y_true, y_pred: y_pred}, optimizer='adadelta')

model.summary()  # print a summary representation of your model.
plot_model(model, to_file='CRNN_with_STN.png', show_shapes=True)

model_save_path = '/home/junbo/PycharmProjects/test0_mnist/models/weights_best_STN.{epoch:02d}-{loss:.2f}.hdf5'
checkpoint = ModelCheckpoint(model_save_path, monitor='loss', verbose=1, save_best_only=True, mode='min')

# model.load_weights('/home/junbo/PycharmProjects/test0_mnist/models/weights_best.04-1.01.hdf5')
model.fit_generator(img_gen(input_shape=bn_shape), steps_per_epoch=2000, epochs=100, verbose=1,
                    callbacks=[evaluator,
                               # checkpoint,
                               TensorBoard(log_dir='/home/junbo/PycharmProjects/test0_mnist/CRC_n/paper_log')])

base_model.save('/home/junbo/PycharmProjects/test0_mnist/models/weights_for_predict_STN.hdf5')
