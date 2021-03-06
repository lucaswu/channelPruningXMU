from xmumodel.model import Model
from xmuutil import utils
import tensorflow as tf
import tensorlayer.layers as tl
import math

"""
An implementation of EDSR used for
super-resolution of images as described in:

`Image Super-Resolution Using Dense Skip Connections`
(http://openaccess.thecvf.com/content_ICCV_2017/papers/Tong_Image_Super-Resolution_Using_ICCV_2017_paper.pdf)

"""


class RWaveletNet(Model):
    def __init__(self, img_size=512, feature_size=16, scale=4, skip = 0):
        num_layers = math.log(img_size, 2.0) - 2
        self.skip = skip
        Model.__init__(self, img_size, num_layers, feature_size, scale, 3)



    def buildModel(self):
        print("Building Recurrent WaveletNet...")

        # input layers
        x = []
        for i in range(self.num_layers):
            x.append(tl.InputLayer(self.input, name='inputlayer%d'%(i+1)))

        '''
        extract low level feature
        In Paper <Densely Connected Convolutional Networks>,the filter size here is 7*7
        and followed by a max pool layer
        upscale_input = tl.Conv2d(x,self.feature_size, [7, 7], act = None, name = 'conv0')
        upscale_input = tl.MaxPool2d(upscale_input, [3,3], [2,2], name = 'maxpool0')
        '''
        upscale_input = tl.Conv2d(x, self.feature_size, [3, 3], act=None, name='conv0')

        # dense-net
        '''
        using SRDenseNet_All model :
        all levels of features(output of dense block) are combined 
        via skip connections as input for reconstructing the HR images
        x
        |\
        | \
        |  dense blockl layer
        | /
        |/
        x1
        |
        [x,x1] (concat)
        '''
        x = upscale_input
        for i in range(self.dense_block):
            # the output of dense blocl
            x = self.__denseBlock(x, self.growth_rate, self.num_layers, [3, 3], layer=i)
            # concat
            upscale_input = tl.ConcatLayer([upscale_input, x], concat_dim=3, name='denseblock%d/concat_output' % (i))

        '''
        bottleneck layer
        In Paper <Image Super-Resolution Using Dense Skip Connections>
        The channel here is 256
        '''
        upscale_input = tl.Conv2d(upscale_input, self.bottleneck_size, [1, 1], act=None, name='bottleneck')

        '''
        Paper <Densely Connected Convolutional Networks> using deconv layer to upscale the output
        here provide two methods here: deconv, subpixel
        '''
        # subpixel to upscale
        if self.is_subpixel:
            upscale_output = tl.Conv2d(upscale_input, self.bottleneck_size, [3, 3], act=None, name='s1/1')
            upscale_output = tl.SubpixelConv2d(upscale_output, scale=2, act=tf.nn.relu, name='pixelshufferx2/1')

            upscale_output = tl.Conv2d(upscale_output, self.bottleneck_size, [3, 3], act=None, name='s1/2')
            upscale_output = tl.SubpixelConv2d(upscale_output, scale=2, act=tf.nn.relu, name='pixelshufferx2/2')

            if self.scale == 8:
                upscale_output = tl.Conv2d(upscale_output, self.bottleneck_size, [3, 3], act=None, name='s1/3')
                upscale_output = tl.SubpixelConv2d(upscale_output, scale=2, act=tf.nn.relu, name='pixelshufferx2/3')
        # deconv to upscale
        else:
            # if scale is 8,using 3 deconv layers
            # is scale is 4,using 2 deconv layers
            width, height = int(upscale_input.outputs.shape[1]), int(upscale_input.outputs.shape[2])
            upscale_output, feature_size, width, height = self.__deconv(upscale_input, self.bottleneck_size, width,
                                                                        height, name='deconv0')
            upscale_output, feature_size, width, height = self.__deconv(upscale_output, feature_size, width, height,
                                                                        name='deconv1')
            if self.scale == 8:
                upscale_output, feature_size, width, height = self.__deconv(upscale_output, feature_size, width, height,
                                                                            name='deconv2')

        # reconstruction layer
        output = tl.Conv2d(upscale_output, self.output_channels, [3, 3], act=tf.nn.relu, name='lastLayer')

        self.output = output.outputs

        self.cacuLoss(output)

        # Tensorflow graph setup... session, saver, etc.
        self.sess = tf.Session()
        self.saver = tf.train.Saver()
        print("Done building!")

    '''
    the implementation of dense block
    a denseblock is defined in the paper as
        x
        |\
        | \
        |  BN
        |  relu
        |  conv2d
        | /
        |/
        x1
        |
        [x,x1](concat)

    for a dense block which has n layers,the output is [x,x1,x2....xn]
    while xi mean the output of i-th layers in this dense block

    x: input to pass through the denseblock
    '''

    def __denseBlock(self, x, growth_rate=16, num_layers=8, kernel_size=[3, 3], layer=0):
        dense_block_output = x
        for i in range(num_layers):
            '''
            In Paper <Densely Connected Convolutional Networks>
            each composite function contains three consecutive operations:
            batch normalization(BN), followed by a rectified linear unit (ReLU) and a 3*3 convolution (Conv).
            '''
            if self.is_bn:
                x = tl.BatchNormLayer(x, name='denseblock%d/BN%d' % (layer, i))
            x = ReluLayer(x, name='denseblock%d/relu%d' % (layer, i))
            x = tl.Conv2d(x, growth_rate, kernel_size, name='denseblock%d/conv%d' % (layer, i))
            # concat the output of layer
            dense_block_output = tl.ConcatLayer([dense_block_output, x], concat_dim=3,
                                                name='denseblock%d/concat%d' % (layer, i))
            x = dense_block_output

        return dense_block_output

    '''
    devonc layer
    for the input shape is  n * width * height * feature_size
    the output shape of the deconv layers is n * (width * 2) * (height * 2) * (feature_size / 2)
    '''

    def __deconv(self, x, feature_size, width, height, name='deconv2'):
        feature_size = feature_size // 2
        width, height = width * 2, height * 2
        # deconv layer
        deconv_output = tl.DeConv2d(x, feature_size, [3, 3], [width, height], act=tf.nn.relu, name=name)
        return deconv_output, feature_size, width, height