# MIT License
#
# Copyright (c) 2020 CNRS
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


from functools import cached_property, lru_cache
from typing import Optional

import torch
import torch.nn as nn
from einops import rearrange
from pyannote.core import SlidingWindow
from torchaudio.transforms import MFCC

from pyannote.audio.core.model import Model
from pyannote.audio.core.task import Task


class SimpleSegmentationModel(Model):
    def __init__(
        self,
        sample_rate: int = 16000,
        num_channels: int = 1,
        task: Optional[Task] = None,
    ):
        super().__init__(sample_rate=sample_rate, num_channels=num_channels, task=task)

        self.mfcc = MFCC(
            sample_rate=self.hparams.sample_rate,
            n_mfcc=40,
            dct_type=2,
            norm="ortho",
            log_mels=False,
        )

        self.lstm = nn.LSTM(
            self.mfcc.n_mfcc * self.hparams.num_channels,
            32,
            num_layers=1,
            batch_first=True,
            bidirectional=True,
        )

    @lru_cache
    def num_frames(self, num_samples: int) -> int:
        """Compute number of output frames for a given number of input samples

        Parameters
        ----------
        num_samples : int
            Number of input samples

        Returns
        -------
        num_frames : int
            Number of output frames

        Source
        ------
        https://pytorch.org/docs/stable/generated/torch.stft.html#torch.stft

        """

        hop_length = self.mfcc.MelSpectrogram.spectrogram.hop_length
        n_fft = self.mfcc.MelSpectrogram.spectrogram.n_fft
        center = self.mfcc.MelSpectrogram.spectrogram.center
        return int(
            1 + num_samples // hop_length
            if center
            else 1 + (num_samples - n_fft) // hop_length
        )

    def receptive_field_size(self, num_frames: int = 1) -> int:
        """Compute receptive field size

        Parameters
        ----------
        num_frames : int, optional
            Number of frames in the output signal

        Returns
        -------
        receptive_field_size : int
            Receptive field size
        """

        hop_length = self.mfcc.MelSpectrogram.spectrogram.hop_length
        n_fft = self.mfcc.MelSpectrogram.spectrogram.n_fft
        center = self.mfcc.MelSpectrogram.spectrogram.center

        if center:
            return (num_frames - 1) * hop_length
        else:
            return (num_frames - 1) * hop_length + n_fft

    @cached_property
    def receptive_field(self) -> SlidingWindow:
        """Compute receptive field

        Returns
        -------
        receptive field : SlidingWindow

        Source
        ------
        https://distill.pub/2019/computing-receptive-fields/

        """

        # duration of the receptive field of each output frame
        duration = (
            self.mfcc.MelSpectrogram.spectrogram.win_length / self.hparams.sample_rate
        )

        # step between the receptive field region of two consecutive output frames
        step = (
            self.mfcc.MelSpectrogram.spectrogram.hop_length / self.hparams.sample_rate
        )

        return SlidingWindow(start=0.0, duration=duration, step=step)

    @property
    def dimension(self) -> int:
        """Dimension of output"""
        if isinstance(self.specifications, tuple):
            raise ValueError("SimpleSegmentationModel does not support multi-tasking.")

        if self.specifications.powerset:
            return self.specifications.num_powerset_classes
        else:
            return len(self.specifications.classes)

    def build(self):
        # define task-dependent layers

        self.classifier = nn.Linear(32 * 2, self.dimension)
        self.activation = self.default_activation()

    def forward(self, waveforms: torch.Tensor) -> torch.Tensor:
        """

        Parameters
        ----------
        waveforms : (batch, time, channel)

        Returns
        -------
        scores : (batch, time, classes)
        """
        # extract MFCC
        mfcc = self.mfcc(waveforms)
        # pass MFCC sequeence into the recurrent layer
        output, hidden = self.lstm(rearrange(mfcc, "b c f t -> b t (c f)"))
        # apply the final classifier to get logits
        return self.activation(self.classifier(output))
