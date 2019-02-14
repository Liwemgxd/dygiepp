import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F
from overrides import overrides

from allennlp.data import Vocabulary
from allennlp.models.model import Model
from allennlp.modules.token_embedders import Embedding
from allennlp.modules import FeedForward
from allennlp.modules import Seq2SeqEncoder, TimeDistributed, TextFieldEmbedder, Pruner
from allennlp.modules.span_extractors import SelfAttentiveSpanExtractor, EndpointSpanExtractor
from allennlp.nn import util, InitializerApplicator, RegularizerApplicator
from allennlp.training.metrics import ConllCorefScores, F1Measure

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name

class NERTagger(Model):
    """
    Named entity recognition module of DyGIE model.

    Parameters
    ----------
    mention_feedforward : ``FeedForward``
        This feedforward network is applied to the span representations which is then scored
        by a linear layer.
    feature_size: ``int``
        The embedding size for all the embedded features, such as distances or span widths.
    spans_per_word: float, required.
        A multiplier between zero and one which controls what percentage of candidate mention
        spans we retain with respect to the number of words in the document.
    lexical_dropout: ``int``
        The probability of dropping out dimensions of the embedded text.
    initializer : ``InitializerApplicator``, optional (default=``InitializerApplicator()``)
        Used to initialize the model parameters.
    regularizer : ``RegularizerApplicator``, optional (default=``None``)
        If provided, will be used to calculate the regularization penalty during training.
    """

    def __init__(self,
                 vocab: Vocabulary,
                 mention_feedforward: FeedForward,
                 feature_size: int,
                 spans_per_word: float,
                 # initializer: InitializerApplicator = InitializerApplicator(), # TODO(dwadden add this).
                 regularizer: Optional[RegularizerApplicator] = None) -> None:
        super(NERTagger, self).__init__(vocab, regularizer)

        self.number_of_ner_classes = vocab.get_vocab_size('ner_labels')

        #feedforward_scorer = torch.nn.Sequential(
        #    TimeDistributed(mention_feedforward),
        #    TimeDistributed(torch.nn.Linear(mention_feedforward.get_output_dim(), 1))
        #)
        #self._mention_pruner = Pruner(feedforward_scorer)

        self.final_network = torch.nn.Sequential(
            TimeDistributed(mention_feedforward),
            TimeDistributed(torch.nn.Linear(
                                mention_feedforward.get_output_dim(),
                                self.number_of_ner_classes - 1)
            )
        )

        self.loss_function = torch.nn.CrossEntropyLoss()

        # TODO(dwadden) Add this.
        #initializer(self)

        self._ner_metrics = [F1Measure(i) for i in range(1, self.number_of_ner_classes)]
        self._ner_avg_metrics = F1Measure(-1)

    @overrides
    def forward(self,  # type: ignore
                spans: torch.IntTensor,
                span_mask: torch.IntTensor,
                span_embeddings: torch.IntTensor,
                sentence_lengths: torch.Tensor,
                max_sentence_length: int,
                ner_labels: torch.IntTensor = None,
                metadata: List[Dict[str, Any]] = None) -> Dict[str, torch.Tensor]:

        """
        TODO(dwadden) Write documentation.
        """

        #Shape: (Batch size, Number of Spans, Span Embedding Size)
        #span_embeddings

        num_spans = spans.size(1)

        ner_scores = self.final_network(span_embeddings)
        dummy_dims = [ner_scores.size(0), ner_scores.size(1), 1]
        dummy_scores = ner_scores.new_zeros(*dummy_dims)
        ner_scores = torch.cat((dummy_scores, ner_scores), -1)

        # Shape: (batch_size, num_spans_to_keep)
        _, predicted_ner = ner_scores.max(2)

        output_dict = {"spans": spans,
                       "predicted_ner": predicted_ner}

        if ner_labels is not None:
            for metric in self._ner_metrics:
                metric(ner_scores, ner_labels, span_mask)
            loss = util.sequence_cross_entropy_with_logits(ner_scores, ner_labels, span_mask)
            output_dict["loss"] = loss

        if metadata is not None:
            output_dict["document"] = [x["sentence"] for x in metadata]
        self.decode(output_dict)
        return output_dict


    @overrides
    def decode(self, output_dict: Dict[str, torch.Tensor]):
        predicted_ner_batch = output_dict["predicted_ner"].detach().cpu()
        spans_batch = output_dict["spans"].detach().cpu()
        
        res = []
        for spans, predicted_NERs in zip(spans_batch, predicted_ner_batch):
            res.append([])
            for span, ner in zip(spans, predicted_NERs):
                if ner>0:
                    res[-1].append([int(span[0]), int(span[1]), int(ner)])

        return res

    @overrides
    def get_metrics(self, reset: bool = False) -> Dict[str, float]:
        metrics = [metric.get_metric(reset) for metric in self._ner_metrics]
        self._ner_avg_metrics._true_positives = sum(metric._true_positives for metric in self._ner_metrics)
        self._ner_avg_metrics._false_positives = sum(metric._false_positives for metric in self._ner_metrics)
        self._ner_avg_metrics._true_negatives = sum(metric._true_negatives for metric in self._ner_metrics)
        self._ner_avg_metrics._false_negatives = sum(metric._false_negatives for metric in self._ner_metrics)
        ner_precision, ner_recall, ner_f1 = self._ner_avg_metrics.get_metric(reset)
        print('---------metrics---------')
        print(metrics)
        print('---------metrics---------')
        print(ner_precision, ner_recall, ner_f1)
        print('-------XXMetricsXX-------')
        
        return {"ner_precision": ner_precision,
                "ner_recall": ner_recall,
                "ner_f1": ner_f1}
        return {"ner_precision": sum(el[0] for el in metrics)/len(metrics),
                "ner_recall": sum(el[1] for el in metrics)/len(metrics),
                "ner_f1": sum(el[2] for el in metrics)/len(metrics)}
