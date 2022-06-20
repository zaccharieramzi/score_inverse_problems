# -*- coding: utf-8 -*-
"""FFT and non-uniform FFT (NUFFT) functions.

"""
import jax.numpy as jnp
import numpy as np
from transforms import util, interp

from math import ceil

__all__ = ['fft', 'ifft', 'nufft']


def fft(input, oshape=None, axes=None, center=True, norm=None):
  """FFT function that supports centering.

  Args:
      input (array): input array.
      oshape (None or array of ints): output shape.
      axes (None or array of ints): Axes over which to compute the FFT.
      norm (None or ``"ortho"``): Keyword to specify the normalization mode.

  Returns:
      array: FFT result of dimension oshape.

  See Also:
      :func:`numpy.fft.fftn`

  """
  if not np.issubdtype(input.dtype, jnp.complexfloating):
    input = input.astype(jnp.complex64)

  if center:
    output = _fftc(input, oshape=oshape, axes=axes, norm=norm)
  else:
    output = jnp.fft.fftn(input, s=oshape, axes=axes, norm=norm)

  if np.issubdtype(input.dtype,
                   jnp.complexfloating) and input.dtype != output.dtype:
    output = output.astype(input.dtype, copy=False)

  return output


def ifft(input, oshape=None, axes=None, center=True, norm=None):
  """IFFT function that supports centering.

  Args:
      input (array): input array.
      oshape (None or array of ints): output shape.
      axes (None or array of ints): Axes over which to compute
          the inverse FFT.
      norm (None or ``"ortho"``): Keyword to specify the normalization mode.

  Returns:
      array of dimension oshape.

  See Also:
      :func:`numpy.fft.ifftn`

  """
  if not np.issubdtype(input.dtype, jnp.complexfloating):
    input = input.astype(jnp.complex64)

  if center:
    output = _ifftc(input, oshape=oshape, axes=axes, norm=norm)
  else:
    output = jnp.fft.ifftn(input, s=oshape, axes=axes, norm=norm)

  if np.issubdtype(input.dtype,
                   jnp.complexfloating) and input.dtype != output.dtype:
    output = output.astype(input.dtype)

  return output


def nufft(input, coord, oversamp=1.25, width=4):
  """Non-uniform Fast Fourier Transform.

  Args:
      input (array): input signal domain array of shape
          (..., n_{ndim - 1}, ..., n_1, n_0),
          where ndim is specified by coord.shape[-1]. The nufft
          is applied on the last ndim axes, and looped over
          the remaining axes.
      coord (array): Fourier domain coordinate array of shape (..., ndim).
          ndim determines the number of dimensions to apply the nufft.
          coord[..., i] should be scaled to have its range between
          -n_i // 2, and n_i // 2.
      oversamp (float): oversampling factor.
      width (float): interpolation kernel full-width in terms of
          oversampled grid.
      n (int): number of sampling points of the interpolation kernel.

  Returns:
      array: Fourier domain data of shape
          input.shape[:-ndim] + coord.shape[:-1].

  References:
      Fessler, J. A., & Sutton, B. P. (2003).
      Nonuniform fast Fourier transforms using min-max interpolation
      IEEE Transactions on Signal Processing, 51(2), 560-574.
      Beatty, P. J., Nishimura, D. G., & Pauly, J. M. (2005).
      Rapid gridding reconstruction with a minimal oversampling ratio.
      IEEE transactions on medical imaging, 24(6), 799-808.

  """
  ndim = coord.shape[-1]
  beta = np.pi * (((width / oversamp) * (oversamp - 0.5)) ** 2 - 0.8) ** 0.5
  os_shape = _get_oversamp_shape(input.shape, ndim, oversamp)

  # Apodize
  output = _apodize(input, ndim, oversamp, width, beta)

  # Zero-pad
  output /= util.prod(input.shape[-ndim:]) ** 0.5
  output = util.resize(output, os_shape)

  # FFT
  output = fft(output, axes=range(-ndim, 0), norm=None)

  # Interpolate
  coord = _scale_coord(coord, input.shape, oversamp)
  output = interp.interpolate(output, coord, kernel='kaiser_bessel', width=width, param=beta)
  output /= width ** ndim

  return output


def nufft_adjoint(input, coord, oshape, oversamp=1.25, width=4):
  """Adjoint non-uniform Fast Fourier Transform.

  Args:
      input (array): input Fourier domain array of shape
          (...) + coord.shape[:-1]. That is, the last dimensions
          of input must match the first dimensions of coord.
          The nufft_adjoint is applied on the last coord.ndim - 1 axes,
          and looped over the remaining axes.
      coord (array): Fourier domain coordinate array of shape (..., ndim).
          ndim determines the number of dimension to apply nufft adjoint.
          coord[..., i] should be scaled to have its range between
          -n_i // 2, and n_i // 2.
      oshape (tuple of ints): output shape of the form
          (..., n_{ndim - 1}, ..., n_1, n_0).
      oversamp (float): oversampling factor.
      width (float): interpolation kernel full-width in terms of
          oversampled grid.
      n (int): number of sampling points of the interpolation kernel.

  Returns:
      array: signal domain array with shape specified by oshape.

  See Also:
      :func:`sigpy.nufft.nufft`

  """
  ndim = coord.shape[-1]
  beta = np.pi * (((width / oversamp) * (oversamp - 0.5)) ** 2 - 0.8) ** 0.5
  oshape = list(oshape)

  os_shape = _get_oversamp_shape(oshape, ndim, oversamp)

  # Gridding
  coord = _scale_coord(coord, oshape, oversamp)
  output = interp.gridding(input, coord, os_shape,
                           kernel='kaiser_bessel', width=width, param=beta)
  # import sigpy
  # output = sigpy.interp.gridding(np.array(input), np.array(coord), os_shape,
  #                                kernel='kaiser_bessel', width=width, param=beta)
  output /= width ** ndim

  # IFFT
  output = ifft(output, axes=range(-ndim, 0), norm=None)

  # Crop
  output = util.resize(output, oshape)
  output *= util.prod(os_shape[-ndim:]) / util.prod(oshape[-ndim:]) ** 0.5

  # Apodize
  output = _apodize(output, ndim, oversamp, width, beta)

  return output


def _fftc(input, oshape=None, axes=None, norm=None):
  ndim = input.ndim
  axes = util._normalize_axes(axes, ndim)

  if oshape is None:
    oshape = input.shape

  tmp = util.resize(input, oshape)
  tmp = jnp.fft.ifftshift(tmp, axes=axes)
  tmp = jnp.fft.fftn(tmp, axes=axes, norm=norm)
  output = jnp.fft.fftshift(tmp, axes=axes)
  return output


def _ifftc(input, oshape=None, axes=None, norm=None):
  ndim = input.ndim
  axes = util._normalize_axes(axes, ndim)

  if oshape is None:
    oshape = input.shape

  tmp = util.resize(input, oshape)
  tmp = jnp.fft.ifftshift(tmp, axes=axes)
  tmp = jnp.fft.ifftn(tmp, axes=axes, norm=norm)
  output = jnp.fft.fftshift(tmp, axes=axes)
  return output


def _scale_coord(coord, shape, oversamp):
  ndim = coord.shape[-1]
  scale = np.ceil(oversamp * np.array(shape[-ndim:])) / shape[-ndim:]
  shift = np.ceil(oversamp * np.array(shape[-ndim:])) // 2
  return coord * scale + shift


def _get_oversamp_shape(shape, ndim, oversamp):
  return list(shape)[:-ndim] + [ceil(oversamp * i) for i in shape[-ndim:]]


def _apodize(input, ndim, oversamp, width, beta):
  output = input
  for a in range(-ndim, 0):
    i = output.shape[a]
    os_i = ceil(oversamp * i)
    idx = np.arange(i, dtype=output.dtype)

    # Calculate apodization
    apod = (beta ** 2 - (np.pi * width * (idx - i // 2) / os_i) ** 2) ** 0.5
    apod /= np.sinh(apod)
    output *= apod.reshape([i] + [1] * (-a - 1))

  return output


def estimate_shape(coord):
  """Estimate array shape from coordinates.
  Shape is estimated by the different between maximum and minimum of
  coordinates in each axis.
  Args:
      coord (array): Coordinates.
  """
  ndim = coord.shape[-1]
  shape = [int(coord[..., i].max() - coord[..., i].min())
           for i in range(ndim)]

  return shape
