# Licensed under a 3-clause BSD style license - see LICENSE.rst

from astropy.tests.helper import pytest
from ..combine import accumulate
import numpy as np
import numpy.ma as ma


def test_invalid_types():
    data = np.zeros((10,), dtype=[('wlen',float), ('flux', float)])
    with pytest.raises(ValueError):
        accumulate(data1_in=0, data2_in=data)
    with pytest.raises(ValueError):
        accumulate(data1_in=data, data2_in=0)
    with pytest.raises(ValueError):
        accumulate(data1_in=data, data2_in=data, data_out=0)


def test_no_common_fields():
    data1 = np.zeros((10,), dtype=[('wlen1',float), ('flux1', float)])
    data2 = np.zeros((10,), dtype=[('wlen2',float), ('flux2', float)])
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2)


def test_incompatible_shapes():
    data1 = np.zeros((10,), dtype=[('wlen',float), ('flux', float)])
    data2 = np.zeros((11,), dtype=[('wlen',float), ('flux', float)])
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2)


def test_invalid_join():
    data1 = np.zeros((10,), dtype=[('wlen',float), ('flux1', float)])
    data2 = np.zeros((10,), dtype=[('wlen',float), ('flux2', float)])
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2, join=0)
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2, join='flux1')
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2, join='flux12')


def test_invalid_add():
    data1 = np.zeros((10,), dtype=[('wlen',float), ('f', float), ('f1', float)])
    data2 = np.zeros((10,), dtype=[('wlen',float), ('f', float), ('f2', float)])
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2, add=0)
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2, add=('f', 1))
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2, add='f1')


def test_invalid_weight():
    data1 = np.zeros((10,), dtype=[('wlen',float), ('f', float), ('w1', float)])
    data2 = np.zeros((10,), dtype=[('wlen',float), ('f', float), ('w2', float)])
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2, add='f', weight=0)
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2, add='f', weight='w')
    with pytest.raises(ValueError):
        accumulate(data1_in=data1, data2_in=data2, add='f', weight='w1')