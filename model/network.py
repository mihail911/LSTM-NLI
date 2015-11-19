""" Defines the entire network
"""

import layers
import numpy as np
import theano
import theano.tensor as T

from model.embeddings import EmbeddingTable
from model.layers import HiddenLayer
from util.utils import convertLabelsToMat


class Network(object):
    """
    Represents entire network.
    """
    def __init__(self, numTimestepsPremise=1, numTimestepsHypothesis=1,
                 dimHidden=2, dimInput=2, embedData=None, trainData=None,
                 trainDataStats=None, valData=None, valDataStats=None,
                 testData=None, testDataStats=None,
                 trainLabels=None, valLabels=None, testLabels=None):
        """
        :param numTimesteps: Number of timesteps to unroll network for.
        :param dataPath: Path to file with precomputed word embeddings
        :param batchSize: Number of samples to use in each iteration of
                         training
        :param optimizer: Optimization algorithm to use for training. Will
                          eventually support adagrad, etc.
        """
        self.numTimestepsPremise = numTimestepsPremise
        self.numTimestepsHypothesis = numTimestepsHypothesis
        self.embeddingTable = EmbeddingTable(embedData)

        # Paths to all data files
        self.trainData = trainData
        self.trainDataStats = trainDataStats
        self.trainLabels = trainLabels
        self.valData = valData
        self.valDataStats = valDataStats
        self.valLabels = valLabels
        self.testData = testData
        self.testDataStats = testDataStats
        self.testLabels = testLabels


        self.dimInput = dimInput
        self.dimHidden = dimHidden
        self.inputMat = None # To store matrix of data input

        self.premiseLSTMName = "premiseLayer"
        self.hypothesisLSTMName = "hypotheisLayer"


    def getMinibatchesIdx(self, numDataPoints, minibatchSize, shuffle=False):
        """
        Used to shuffle the dataset at each iteration. Return list of
        (batch #, batch) pairs.
        """

        idxList = np.arange(numDataPoints, dtype="int32")

        if shuffle:
            np.random.shuffle(idxList)

        minibatches = []
        minibatchStart = 0
        for i in xrange(numDataPoints // minibatchSize):
            minibatches.append(idxList[minibatchStart:
                                        minibatchStart + minibatchSize])
            minibatchStart += minibatchSize

        if (minibatchStart != numDataPoints):
            # Make a minibatch out of what is left
            minibatches.append(idxList[minibatchStart:])

        return zip(range(len(minibatches)), minibatches)


    def buildModel(self):
        """
        Handles building of model, including initializing necessary parameters, defining
        loss functions, etc.
        """
        self.hiddenLayerPremise = HiddenLayer(self.dimInput, self.dimHidden, "premiseLayer")
        del self.hiddenLayerPremise.params["weightsCat_premiseLayer"] # Need to make sure not differentiating with respect to Wcat of premise
        del self.hiddenLayerPremise.params["biasCat_premiseLayer"] # May want to find cleaner way to deal with this later

        self.hiddenLayerHypothesis = HiddenLayer(self.dimInput, self.dimHidden, "hypothesisLayer")


    def trainFunc(self, inputPremise, inputHypothesis, yTarget, learnRate):
        """
        Defines theano training function for layer, including forward runs and backpropagation.
        Takes as input the necessary symbolic variables.
        """
        self.hiddenLayerPremise.forwardRun(inputPremise, 1)
        premiseOutputHidden = self.hiddenLayerPremise.finalHiddenVal
        premiseOutputCandidate = self.hiddenLayerPremise.finalCandidateVal

        self.hiddenLayerHypothesis.setInitialLayerParams(premiseOutputHidden, premiseOutputCandidate)
        cost = self.hiddenLayerHypothesis.costFunc(inputHypothesis, yTarget, 1)

        grads = self.hiddenLayerHypothesis.computeGrads(inputHypothesis, yTarget, cost)
        paramUpdates = self.hiddenLayerHypothesis.sgd(grads, learnRate)
        trainF = theano.function([inputPremise, inputHypothesis, yTarget, learnRate], updates=paramUpdates, name='trainNet')

        return trainF


# TODO: Write code for saving trained models!
    def train(self, numEpochs=1, batchSize=1):
        """
        Takes care of training model, including propagation of errors and updating of
        parameters.
        """
        trainPremiseIdxMat, trainHypothesisIdxMat = self.embeddingTable.convertDataToIdxMatrices(
                                self.trainData, self.trainDataStats)
        trainGoldLabel = convertLabelsToMat(self.trainLabels)

        valPremiseIdxMat, valHypothesisIdxMat = self.embeddingTable.convertDataToIdxMatrices(
                                self.valData, self.valDataStats)
        valGoldLabel = convertLabelsToMat(self.valLabels)


        inputPremise = T.dtensor3("inputPremise")
        inputHypothesis = T.dtensor3("inputHypothesis")
        yTarget = T.dmatrix("yTarget")
        learnRate = T.scalar(name="learnRate")

        for epoch in xrange(numEpochs):
            print "Epoch number: %d" %(epoch)

            minibatches = self.getMinibatchesIdx(trainGoldLabel, batchSize)
            for _, minibatch in minibatches:
                batchPremise = trainPremiseIdxMat[minibatch]
                batchHypothesis = trainHypothesisIdxMat[minibatch]
                batchLabels = trainGoldLabel[minibatch]

            self.hiddenLayerHypothesis.appendParams(self.hiddenLayerPremise.params) # May have to do this every time?

            premiseSent = np.random.randn(1,1,2)
            hypothesisSent = np.random.randn(1,1,2)
            yTargetVal = np.array([[0., 1., 0.]], dtype=np.float64)
            learnRateVal = 0.5

            trainNetFunc = self.trainFunc(inputPremise, inputHypothesis, yTarget, learnRate)

            gradOut = trainNetFunc(premiseSent, hypothesisSent, yTargetVal, learnRateVal)
            newPremiseGrads = self.hiddenLayerHypothesis.getPremiseGrads()
            self.hiddenLayerPremise.updateParams(newPremiseGrads)

            trainNetFunc(premiseSent, hypothesisSent, yTargetVal, learnRateVal)


    def predictFunc(self, symPremise, symHypothesis):
        """
        Produces a theano prediction function for outputting the label of a given input.
        Takes as input a symbolic premise and a symbolic hypothesis.
        :return: Theano function for generating probability distribution over labels.
        """
        self.hiddenLayerPremise.forwardRun(symPremise, 1)
        premiseOutputHidden = self.hiddenLayerPremise.finalHiddenVal
        premiseOutputCandidate = self.hiddenLayerPremise.finalCandidateVal

        # Run through hypothesis LSTM
        self.hiddenLayerHypothesis.setInitialLayerParams(premiseOutputHidden, premiseOutputCandidate)
        self.hiddenLayerHypothesis.forwardRun(symHypothesis, 1)
        catOutput = self.hiddenLayerHypothesis.projectToCategories()
        softMaxOut = self.hiddenLayerHypothesis.applySoftmax(catOutput)
        #print "softMaxOut", softMaxOut.eval()
        labelIdx = softMaxOut.argmax(axis=1)

        return theano.function([symPremise, symHypothesis], labelIdx, name="predictLabelsFunction")


    def predict(self, premiseSent, hypothesisSent, predictFunc):
        """
        Output Labels for given premise/hypothesis sentences pair.
        :param premiseSent:
        :param hypothesisSent:
        :param predictFunc:
        :return: Label category from among "entailment", "contradiction", "neutral"
        """
        categories = ["entailment", "contradiction", "neutral"]
        labelIdx = predictFunc(premiseSent, hypothesisSent)
        #print "label idx: ", labelIdx
        labelCategories = []
        for idx in labelIdx:
            labelCategories.append(categories[idx])

        return labelCategories
