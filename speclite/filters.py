# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""Support for reading and applying filter response curves.

See :doc:`/filters` for more information.

Attributes
----------
default_wavelength_unit : :class:`astropy.units.Unit`
    The default wavelength units assumed when units are not specified.
    The same units are used to store wavelength values in internal arrays.
"""

import os
import os.path
import glob

import numpy as np

import scipy.interpolate
import scipy.integrate

import astropy.table
import astropy.units
import astropy.utils.data


default_wavelength_unit = astropy.units.Angstrom

_filter_integration_methods = dict(
    trapz= scipy.integrate.trapz,
    simps= scipy.integrate.simps)


def validate_wavelength_array(wavelength, min_length=0):
    """Validate a wavelength array for filter operations.

    Parameters
    ----------
    wavelength : array
        A 1D array of strictly increasing wavelength values with optional
        units.  If units are included, they must be convertible to
        :attr:`default_wavelength_unit`.  Otherwise, the
        :attr:`default_wavelength_unit` is assumed.
    min_length : int
        The minimum required length of the wavelength array.

    Raises
    ------
    ValueError
        Wavelength array is not 1D, or not strictly increasing, or below
        the minimum length.
    astropy.units.UnitConversionError
        The wavelength array has units that are not convertible to
        :attr:`default_wavelength_unit`
    """
    wavelength = np.asanyarray(wavelength)
    if len(wavelength.shape) != 1:
        raise ValueError('Wavelength array must be 1D.')
    if len(wavelength) < min_length:
        raise ValueError('Minimum length is {0}.'.format(min_length))
    if not np.all(np.diff(wavelength) > 0):
        raise ValueError('Wavelength values must be strictly increasing.')
    try:
        if wavelength.unit != default_wavelength_unit:
            # Try to convert to the default units. This will raise a UnitConversionError
            # if the current units are not convertible to the default units.
            wavelength = wavelength.to(default_wavelength_unit)
    except AttributeError:
        # No units present, so apply the default units.
        wavelength = wavelength * default_wavelength_unit
    return wavelength


def evaluate_function_of_wavelength(function, wavelength):
    """Evaluate a function of wavelength.

    Parameters
    ----------
    function : callable
        Any function that expects a wavelength or array of wavelengths and
        returns its value.  Functions will be called first with wavelength
        units included and then without units included, in which case they
        should treat all wavelengths as having
        :attr:`default_wavelength_unit`. If a function returns a value with
        units, this will be correctly propagated to the output.
    wavelength : astropy.units.Quantity
        Wavelength of array of wavelengths where the function should be
        evaluated.  Wavelengths must have valid units.

    Returns
    -------
    tuple
        Tuple (values, units) of function values at each input wavelength.
    """
    try:
        wavelength = wavelength.to(default_wavelength_unit)
    except (AttributeError, astropy.units.UnitConversionError):
        raise ValueError('Cannot evaluate function for invalid wavelength.')

    function_units = None
    # Try broadcasting our wavelength array with its units.
    try:
        function_values = function(wavelength)
        try:
            function_units = function_values.unit
            function_values = function_values.value
        except AttributeError:
            pass
        return function_values, function_units
    except (TypeError, astropy.units.UnitsError):
        pass
    # Try broadcasting our wavelength array without its units.
    try:
        function_values = function(wavelength.value)
        try:
            function_units = function_values.unit
            function_values = function_values.value
        except AttributeError:
            pass
        return function_values, function_units
    except TypeError:
        pass
    # Try looping over wavelengths and including units.
    try:
        function_values = []
        for wavelength in wavelength.value:
            value = function(wavelength * default_wavelength_unit)
            try:
                if function_units is None:
                    function_units = value.unit
                elif value.unit != function_units:
                    raise RuntimeError('Inconsistent function units.')
                value = value.value
            except AttributeError:
                pass
            function_values.append(value)
        function_values = np.asarray(function_values)
        return function_values, function_units
    except (TypeError, astropy.units.UnitsError):
        pass
    # Try looping over wavelengths and not including units.
    try:
        function_values = []
        for wavelength in wavelength.value:
            value = function(wavelength)
            try:
                if function_units is None:
                    function_units = value.unit
                elif value.unit != function_units:
                    raise RuntimeError('Inconsistent function units.')
                value = value.value
            except AttributeError:
                pass
            function_values.append(value)
        function_values = np.asarray(function_values)
        return function_values, function_units
    except TypeError:
        pass

    # If we get here, none of the above strategies worked.
    raise ValueError('Invalid function.')


class FilterResponse(object):
    """A filter response curve tabulated in wavelength.

    Some standard filters are included in this package and can be initialized
    using :func:`load_filter`.  For example:

    >>> rband = load_filter('sdss2010-r')

    Objects behave like functions that evaluate their response at aribtrary
    wavelengths.  Wavelength units can be specified, or else default to
    :attr:`default_wavelength_unit`:

    >>> round(rband(6000 * astropy.units.Angstrom), 4)
    0.5323
    >>> round(rband(6000), 4)
    0.5323
    >>> round(rband(0.6 * astropy.units.micron), 4)
    0.5323

    Filters can be also evaluated for an arbitrary array of wavelengths,
    returning a numpy array of response values:

    >>> np.round(rband([5980, 6000, 6020]), 4)
    array([ 0.5309,  0.5323,  0.5336])

    The effective wavelength of a filter is defined as the photon-weighted
    mean wavelength:

    .. math::

        \lambda_{eff} \equiv
        \dfrac{\int \lambda R(\lambda) d\lambda/\lambda}
        {\int R(\lambda) d\lambda/\lambda}

    where :math:`R(\lambda)` is our response function.  Use the
    :attr:`effective_wavelength` attribute to access this value:

    >>> print np.round(rband.effective_wavelength, 1)
    6159.3 Angstrom

    Parameters
    ----------
    wavelength : array
        A :func:`valid array <validate_wavelength_array>` of wavelengths.
    response : array
        A dimensionless 1D array of filter response values corresponding to
        each wavelength.  Response values must be non-negative and cannot all
        be zero. The bounding response values must be zero, and the response
        is assumed to be zero outside of the specified wavelength range.
    meta : dict
        A dictionary of metadata which must include values for the keys listed
        :doc:`here </filters>`.  Additional keys are also permitted.

    Attributes
    ----------
    wavelength : :class:`astropy.units.Quantity`
        Array of wavelengths where the filter response is tabulated, including
        units.
    response : numpy.ndarray
        Numpy array of response values passed to our constructor, after
        trimming any extra leading or trailing zero response values.
    meta : dict
        Dictionary of metadata including the keys listed :doc:`here </filters>`.
    interpolator : :class:`scipy.interpolate.interp1d`
        Linear interpolator of our response function that returns zero for
        all values outside our wavelength range.  Should normally be evaluated
        through our :meth:`__call__` convenience method.
    effective_wavelength : :class:`astropy.units.Quantity`
        Mean photon-weighted wavelength of this response function, as
        defined above.

    Raises
    ------
    ValueError
        Invalid wavelength or response input arrays, or missing required keys
        in the input metadata.
    """
    def __init__(self, wavelength, response, meta):

        self.wavelength = validate_wavelength_array(wavelength, min_length=3)
        self.response = np.asanyarray(response)
        if len(self.wavelength) != len(self.response):
            raise ValueError('Arrays must have same length.')

        try:
            if self.response.decompose().unit != u.dimensionless_unscaled:
                raise ValueError('Response must be dimensionless.')
            # Convert response values to a plain numpy array.
            self.response = self.response.value
        except AttributeError:
            # response has no units assigned, which is fine.
            pass

        # Check for a valid response curve.
        if np.any(self.response < 0):
            raise ValueError('Response values must be non-negative.')
        if np.all(self.response == 0):
            raise ValueError('Response values cannot all be zero.')
        if not self.response[0] == 0 and self.response[-1] == 0:
            raise ValueError('Response must go to zero on both sides.')

        # Trim any extra leading and trailing zeros.
        non_zero = np.where(self.response > 0)[0]
        start, stop = non_zero[0] - 1, non_zero[-1] + 2
        if stop - start < len(self.wavelength):
            self.wavelength = self.wavelength[start: stop]
            self.response = self.response[start: stop]

        # Check for the required metadata fields.
        self.meta = dict(meta)
        for required in ('group_name', 'band_name'):
            if required not in self.meta:
                raise ValueError(
                    'Metadata missing required key "{0}".'.format(required))

        # Create a linear interpolator of our response function that returns zero
        # outside of our wavelength range.
        self.interpolator = scipy.interpolate.interp1d(
            self.wavelength.value, self.response, kind='linear',
            copy=False, assume_sorted=True,
            bounds_error=False, fill_value=0.)

        # Calculate this filter's effective wavelength.
        one = astropy.units.Quantity(1.)
        numer = self.convolve_with_function(lambda wlen: one)
        denom = self.convolve_with_function(lambda wlen: one / wlen)
        self.effective_wavelength = numer / denom


    def __call__(self, wavelength):
        """Evaluate the filter response at arbitrary wavelengths.

        Parameters
        ----------
        wavelength : array or float
            A single wavelength value or an array of wavelengths.
            If units are included, they will be correctly interpreted.
            Otherwise :attr:`default_wavelength_unit` is assumed.

        Returns
        -------
        numpy.ndarray
            Numpy array of response values corresponding to each input
            wavelength.

        Raises
        ------
        astropy.units.UnitConversionError
            Input wavelength(s) have unit that is not convertible to
            :attr:`default_wavelength_unit`.
        """
        # Use asanyarray() so that a Quantity with units is not copied here.
        wavelength = np.asanyarray(wavelength)
        try:
            wavelength = wavelength.to(default_wavelength_unit).value
        except AttributeError:
            # No units present, so assume the default units.
            pass
        response = self.interpolator(wavelength)
        # If the input was scalar, return a scalar.
        if response.shape == ():
            response = np.asscalar(response)
        return response


    def convolve_with_function(self, function, method='trapz'):
        """Convolve this response with a function of wavelength.

        Returns a numerical estimate of the convolution integral:

        .. math::

            \int f(\lambda) R(\lambda) d\lambda

        for an arbitrary function :math:`f(\lambda)`, where :math:`R(\lambda)`
        is our response function..  For example, to calculate a filter's
        effective wavelength:

        >>> rband = load_filter('sdss2010-r')
        >>> one = astropy.units.Quantity(1.)
        >>> numer = rband.convolve_with_function(lambda wlen: one)
        >>> denom = rband.convolve_with_function(lambda wlen: one / wlen)
        >>> print np.round(numer / denom, 1)
        6159.3 Angstrom

        Parameters
        ----------
        function : callable
            Any function that expects a wavelength or array of wavelengths and
            returns its value.  Functions will be called first with wavelength
            units included and then without units included, in which case they
            should treat all wavelengths as having
            :attr:`default_wavelength_unit`. If a function returns a value with
            units, this will be correctly propagated to the convolution result.
        method : str
            Specifies the numerical integration scheme to use and must be either
            'trapz' or 'simps', to select the corresponding
            ``scipy.integration`` function. The 'simps' method may be more
            accurate than the default 'trapz' method, but should be used with
            care since it is also less robust and more sensitive to the
            wavelength grid.

        Returns
        -------
        float or astropy.units.Quantity
            Result of the convolution integral.  Units will be included if the
            function returns a value with units.  Otherwise, the implicit units
            of the result are equal to the implicit function value units
            multiplied by :attr:`default_wavelength_unit`.

        Raises
        ------
        ValueError
            Function does not behave as expected or invalid method.
        RuntimeError
            Function returns inconsistent units.
        """
        if method not in _filter_integration_methods.keys():
            raise ValueError(
                'Invalid integration method {0}. Pick one of {1}.'
                .format(method, _filter_integration_methods.keys()))


        function_values, function_units = \
            evaluate_function_of_wavelength(function, self.wavelength)
        function_values *= self.response

        integrator = _filter_integration_methods[method]
        result = integrator(y = function_values, x=self.wavelength.value)
        if function_units is not None:
            result = result * function_units * default_wavelength_unit
        return result


    def convolve_with_array(self, wavelength, values, axis=-1,
                            extrapolate=False, interpolate=False):
        """
        """
        convolution = FilterReponseConvolution(self, wavelength, extrapolate, interpolate)
        return convolution(values, axis=axis)


    def get_effective_wavelength(self):
        pass


class FilterConvolution(object):
    """Convolve a filter response with a tabulated function.

    Most of the computation involved depends only on the tabulated function's
    wavelength grid, and not on the function values, so this class does the
    necessary initialization in its constructor, resulting in a function
    object that can be efficiently re-used with different function values.

    Parameters
    ----------
    response : :class:`FilterResponse` or str
        A FilterResponse object or else a fully qualified name of the form
        "<group_name>-<band_name>", which will be loaded using
        :func:`load_filter`.
    wavelength : array
        A :func:`valid array <validate_wavelength_array>` of wavelengths.
    interpolate : bool
        Allow interpolation of the tabulated function if necessary.
        Interpolation is required if two or more of the wavelengths where the
        filter response is tabulated fall between a consecutive pair of
        input wavelengths. Linear interpolation is then performed to estimate
        the input function at the undersampled filter response wavelengths.
        Interpolation has a performance impact when :meth:`evaluating
        <__call__>` a convlution, so is not enabled by default and can usually
        be avoided with finer sampling of the input function.

    Attributes
    ----------
    response : :class:`FilterResponse`
        The filter response used for this convolution.
    num_wavelength : int
        The number of wavelengths used to tabulate input functions.
    wavelength : :class:`astropy.units.Quantity`
        Array of input wavelengths used for the convolution, with units.
    response_grid : numpy.ndarray
        Array of filter response values evaluated at each ``wavelength``.
    response_slice : slice
        Slice of the input wavelength grid used for convolution.
    interpolate_wavelength : numpy.ndarray or None
        Array of wavelengths where the input function will be interpolated.
    interpolate_response : numpy.ndarray or None
        Array of filter response values at each ``interpolate_wavelength``.
    interpolate_sort_order : numpy.ndarray or None
        Integer array specifying the sort order required to combine
        ``wavelength`` and ``interpolate_wavelength``.
    quad_wavelength : numpy.ndarray
        Array of wavelengths used for numerical quadrature, combining both
        ``wavelength`` and ``interpolate_wavelength``.
    """
    def __init__(self, response, wavelength, interpolate=False):

        if isinstance(response, basestring):
            self.response = load_filter(response)
        else:
            self.response = response
        self.wavelength = validate_wavelength_array(wavelength, min_length=2)
        self.num_wavelength = len(self.wavelength)

        # Check if extrapolation would be required.
        under = (self.wavelength[0] > self.response.wavelength[0])
        over = (self.wavelength[-1] < self.response.wavelength[-1])
        if under or over:
            raise ValueError(
                'Wavelengths do not cover filter response {:.1f}-{:.1f} {}.'
                .format(self.response.wavelength[0].value,
                        self.response.wavelength[-1].value,
                        default_wavelength_unit))

        # Find the smallest slice that covers the non-zero range of the
        # integrand.
        start, stop = 0, len(self.wavelength)
        if self.wavelength[0] < self.response.wavelength[0]:
            start = (np.where(self.wavelength <=
                     self.response.wavelength[0])[0][-1]
        if self.wavelength[-1] > self.response.wavelength[-1]:
            stop = 1 + np.where(
                self.wavelength >= self.response.wavelength[-1])[0][0]

        # Trim the wavelength grid if possible.
        self.response_slice = slice(start, stop)
        if start > 0 or stop < len(self.wavelength):
            self.wavelength = self.wavelength[self.response_slice]

        # Linearly interpolate the filter response to our wavelength grid.
        self.response_grid = self.response(self.wavelength)

        # Test if our grid is samples the response with sufficient density. Our
        # criterion is that at most one internal response wavelength (i.e.,
        # excluding the endpoints which we treat separately) falls between each
        # consecutive pair of our wavelength grid points.
        insert_index = np.searchsorted(
            self.wavelength, self.response.wavelength[1:])
        undersampled = np.diff(insert_index) == 0
        if np.any(undersampled):
            undersampled = 1 + np.where(undersampled)[0]
            if interpolate:
                # Interpolate at each undersampled wavelength.
                self.interpolate_wavelength = (
                    self.response.wavelength[undersampled])
                self.interpolate_response = self.response.response[undersampled]
                self.quad_wavelength = default_wavelength_unit * np.hstack(
                    [self.wavelength.value, self.interpolate_wavelength.value])
                self.interpolate_sort_order = np.argsort(self.quad_wavelength)
                self.quad_wavelength = self.quad_wavelength[
                    self.interpolate_sort_order]
            else:
                raise ValueError(
                    'Wavelengths undersample the response ' +
                    'and interpolate is False.')
        else:
            self.interpolate_wavelength = None
            self.interpolate_response = None
            self.interpolate_sort_order = None
            self.quad_wavelength = self.wavelength

        # Replace the quadrature endpoints with the actual filter endpoints
        # to eliminate any overrun.
        if self.quad_wavelength[0] < self.response.wavelength[0]:
            self.quad_wavelength[0] = self.response.wavelength[0]
        if self.quad_wavelength[-1] > self.response.wavelength[-1]:
            self.quad_wavelength[-1] = self.response.wavelength[-1]


    def __call__(self, values, axis=-1, method='trapz', plot=False):
        """Evaluate the convolution for arbitrary tabulated function values.

        Parameters
        ----------
        values : array or :class:`astropy.units.Quantity`
            Array of function values to use.  Values are assumed to be
            tabulated on our wavelength grid.  Values can be multidimensional,
            in which case an array of convolution results is returned. If
            values have units, then these are propagated to the result.
        axis : int
            In case of multidimensional function values, this specifies the
            index of the axis corresponding to the wavelength dimension.
        method : str
            Specifies the numerical integration scheme to use and must be either
            'trapz' or 'simps', to select the corresponding
            ``scipy.integration`` function. The 'simps' method may be more
            accurate than the default 'trapz' method, but should be used with
            care since it is also less robust and more sensitive to the
            wavelength grids.
        plot : bool
            Displays a plot illustrating how the convolution integrand is
            constructed. Requires that the matplotlib package is installed
            and does not support multidimensional input values.  This option
            is primarily intended for debugging and to generate figures for
            the documentation.
        """
        if method not in _filter_integration_methods.keys():
            raise ValueError(
                'Invalid method "{}", pick one of {}.'
                .format(method, _filter_integration_methods.keys()))

        values = np.asanyarray(values)
        if values.shape[axis] != self.num_wavelength:
            raise ValueError(
                'Expected {} values along axis {}.'
                .format(len(self.wavelength), axis))
        values_slice = [slice(None)] * len(values.shape)
        values_slice[axis] = self.response_slice
        values = values[values_slice]

        try:
            # Remove the units for subsequent calculations. We will re-apply
            # the units to the final result.
            values_unit = values.unit
            values = values.value
        except AttributeError:
            values_unit = None

        if plot:
            if len(values.shape) != 1:
                raise ValueError(
                    'Cannot plot convolution of multidimensional values.')
            import matplotlib.pyplot as plt
            fig, left_axis = plt.subplots()
            # Plot the filter response using the left-hand axis.
            plt.plot(self.response.wavelength.value,
                     self.response.response, 'rx-')
            plt.ylim(0., 1.05 * np.max(self.response.response))
            plt.xlabel('Wavelength (A)')
            left_axis.set_ylabel(
                '{}-{} Filter Response'.format(
                    self.response.meta['group_name'],
                    self.response.meta['band_name']))
            # Use the right-hand axis for the data being filtered.
            right_axis = left_axis.twinx()
            # A kludge to include the left-hand axis label in our legend.
            right_axis.plot([], [], 'r.-', label='filter')
            # Plot the input values using the right-hand axis.
            right_axis.set_ylabel('Function Values')
            right_axis.plot(self.wavelength, values, 'bs-', label='input')
            right_axis.set_ylim(0., 1.1 * np.max(values))

        # Multiply values by the response.
        response_shape = np.ones_like(values.shape, dtype=int)
        response_shape[axis] = len(self.response_grid)
        integrand = values * self.response_grid.reshape(response_shape)

        if self.interpolate_wavelength is not None:
            # Interpolate the input values.
            interpolator = scipy.interpolate.interp1d(
                self.wavelength.value, values, axis=axis, kind='linear',
                copy=False, assume_sorted=True, bounds_error=True)
            interpolated_values = interpolator(
                self.interpolate_wavelength.value)
            if plot:
                # Show the interpolation locations.
                plt.scatter(
                    self.interpolate_wavelength.value, interpolated_values,
                    s=30, marker='o', edgecolor='b', facecolor='none', label='interpolated')
            # Multiply interpolated values by the response.
            response_shape[axis] = len(self.interpolate_wavelength)
            interpolated_integrand = (
                interpolated_values *
                self.interpolate_response.reshape(response_shape))
            # Update the integrand with the interpolated values.
            integrand = np.concatenate(
                (integrand, interpolated_integrand), axis=axis)
            # Resort by wavelength.
            values_slice[axis] = self.interpolate_sort_order
            integrand = integrand[values_slice]

        if plot:
            plt.fill_between(
                self.quad_wavelength.value, integrand,
                color='g', lw=0, alpha=0.25)
            plt.plot(
                self.quad_wavelength.value, integrand,
                'g-', alpha=0.5, label='filtered')
            right_axis.legend(loc='lower center')
            xpad = 0.05 * (
                self.quad_wavelength[-1] - self.quad_wavelength[0]).value
            plt.xlim(self.wavelength[0].value - xpad,
                     self.wavelength[-1].value + xpad)

        # Perform quadrature on self.wavelength and values.
        integrator = _filter_integration_methods[method]
        integral = integrator(
            y=integrand, x=self.quad_wavelength.value, axis=axis)
        if values_unit:
            # Re-apply the input units with an extra factor of the wavelength
            # units. If the input units include something other than u.Angstrom
            # for wavelength, then the output units will not be fully
            # simplified. We could simplify by calling decompose() but this
            # would change ergs to Joules, etc.
            integral = integral * values_unit * default_wavelength_unit
        return integral


# Dictionary of cached FilterResponse objects.
_filter_cache = {}


def load_filter(name, load_from_cache=True, save_to_cache=True, verbose=False):
    """Load a filter response by name.

    See :doc:`/filters` for details on the filter response file format and
    the available standard filters.

    A filter response is normally only loaded from disk the first time this
    function is called, and subsequent calls immediately returned the same
    cached object.  Use the ``verbose`` option for details on how a filter
    is loaded:

    >>> rband = load_filter('sdss2010-r')
    >>> rband = load_filter('sdss2010-r', verbose=True)
    Returning cached filter response "sdss2010-r"

    Use :func:`load_filter_group` to pre-load all bands for a specified
    group of filters.

    Parameters
    ----------
    name : str
        Name of the filter response to load, which should have the format
        "<group_name>-<band_name>".
    load_from_cache : bool
        Return a previously cached response object if available.  Otherwise,
        always load the file from disk.
    save_to_cache : bool
        Remember the returned object so that it can be returned immediately
        from a cache the next time it is requested.
    verbose : bool
        Print verbose information about how this filter is loaded.

    Returns
    -------
    FilterResponse
        A :class:`FilterResponse` object for the requested filter.

    Raises
    ------
    RuntimeError
        File is incorrectly formatted.  This should never happen for the
        files included in the source code distribution.
    """
    if load_from_cache and name in _filter_cache:
        if verbose:
            print('Returning cached filter response "{0}"'.format(name))
        return _filter_cache[name]
    file_name = astropy.utils.data._find_pkg_data_path(
        'data/filters/{0}.ecsv'.format(name))
    if not os.path.isfile(file_name):
        raise ValueError('No such filter "{0}".'.format(name))
    if verbose:
        print('Loading filter response from "{0}".'.format(file_name))
    table = astropy.table.Table.read(
        file_name, format='ascii.ecsv', guess=False)

    if 'wavelength' not in table.colnames:
        raise RuntimeError('Table is missing required wavelength column.')
    wavelength_column = table['wavelength']
    if wavelength_column.unit is None:
        raise RuntimeError('No wavelength column unit specified.')
    wavelength = wavelength_column.data * wavelength_column.unit

    if 'response' not in table.colnames:
        raise RuntimeError('Table is missing required response column.')
    response_column = table['response']
    if response_column.unit is not None:
        raise RuntimeError('Response column has unexpected units.')
    response = response_column.data

    response = FilterResponse(wavelength, response, table.meta)
    if save_to_cache:
        if verbose:
            print('Saving filter response "{0}" in the cache.'.format(name))
        _filter_cache[name] = response
    return response


def load_filter_group(group_name):
    """Find the names of all bands available in a filter response group.

    The returned names are suitable for passing to :func:`load_filter`, and
    all of the named filters will be pre-loaded into the cache after calling
    this function.  Filters are listed in order of increasing effective
    wavelength, for example:

    >>> load_filter_group('sdss2010')
    ['sdss2010-u', 'sdss2010-g', 'sdss2010-r', 'sdss2010-i', 'sdss2010-z']

    Parameters
    ----------
    group_name : str
        Name of the group to load.

    Returns
    -------
    list
        List of names associated with the specified group, in the format
        "<group_name>-<band_name>" expected by :func:`load_filter`. Returns
        an empty list if no bands are available.
    """
    band_names, effective_wavelengths = [], []
    offset = len(group_name) + 1
    filters_path = astropy.utils.data._find_pkg_data_path('data/filters/')
    file_names = glob.glob(
        os.path.join(filters_path, '{0}-*.ecsv'.format(group_name)))
    for file_name in file_names:
        name, _ = os.path.splitext(os.path.basename(file_name))
        band_names.append(name)
        response = load_filter(name)
        effective_wavelengths.append(response.effective_wavelength)

    # Return the names sorted by effective wavelength.
    band_names = [name for (wlen, name) in
                  sorted(zip(effective_wavelengths, band_names))]
    return band_names


def plot_filters(group_name=None, names=None, wavelength_unit=None,
                 wavelength_limits=None, wavelength_scale='linear',
                 legend_loc='upper right', cmap='nipy_spectral', save=None):
    """Plot one or more filter response curves.

    The matplotlib package must be installed to use this function.

    Parameters
    ----------
    group_name : str
        Name of the filter group to plot.
    names : list
        List of filter names to plot.  If ``group_name`` is also specified,
        these should be names of bands within the group.  Otherwise, they
        should be fully qualified names of the form "<group_name>-<band_name>".
    wavelength_unit : :class:`astropy.units.Unit`
        Convert values along the wavelength axis to the specified unit, or
        leave them as :attr:`default_wavelength_unit` if this parameter is None.
    wavelength_limits : tuple or None
        Plot limits to use on the wavelength axis, or select limits
        automatically if this parameter is None.  Units are optional.
    wavelength_scale : str
        Scaling to use for the wavelength axis. See
        :func:`matplotlib.pyplot.yscale` for details.
    legend_loc : str
        Location of the legend to plot, or do not display any legend if this
        value is None.  See :func:`matplotlib.pyplot.legend` for details.
    cmap : str or :class:`matplotlib.colors.Colormap`
        Color map to use for plotting each filter band.  Colors are assigned
        based on each band's effective wavelength, so a spectral color map
        (from blue to red) will give nice results.
    save : str
        Filename to use for saving this plot, or do not save any plot if this
        is None.  See :func:`matplotlib.pyplot.savefig` for details.
    """
    if group_name is not None:
        if names is not None:
            names = ['{0}-{1}'.format(group_name, name) for name in names]
        else:
            names = load_filter_group(group_name)

    if wavelength_unit is None:
        wavelength_unit = default_wavelength_unit

    # Look up the range of effective wavelengths for this set of filters.
    effective_wavelengths = []
    for name in names:
        response = load_filter(name)
        effective_wavelengths.append(response.effective_wavelength.value)
    min_wlen, max_wlen = min(effective_wavelengths), max(effective_wavelengths)

    import matplotlib.pyplot as plt
    import matplotlib.cm as cm

    cmap = cm.get_cmap(cmap)
    fig, ax = plt.subplots()
    plt.xscale(wavelength_scale)
    if wavelength_limits is not None:
        try:
            wlen_min, wlen_max = wavelength_limits
        except TypeError:
            raise ValueError('Invalid wavelength limits.')
        try:
            wlen_min = wlen_min.to(wavelength_unit).value
        except astropy.units.UnitConversionError:
            raise ValueError('Invalid wavelength_unit.')
        except AttributeError:
            pass
        try:
            wlen_max = wlen_max.to(wavelength_unit).value
        except astropy.units.UnitConversionError:
            raise ValueError('Invalid wavelength_unit.')
        except AttributeError:
            pass
        plt.xlim(wlen_min, wlen_max)

    for name, wlen in zip(names, effective_wavelengths):
        response = load_filter(name)
        if max_wlen > min_wlen:
            # Use an approximate spectral color for each band.
            c = cmap(0.1 + 0.8 * (wlen - min_wlen) / (max_wlen - min_wlen))
        else:
            c = 'green'
        wlen = response.wavelength
        try:
            wlen = wlen.to(wavelength_unit)
        except astropy.units.UnitConversionError:
            raise ValueError('Invalid wavelength_unit.')

        plt.fill_between(wlen.value, response.response, color=c, alpha=0.25)
        plt.plot(wlen.value, response.response, color=c, alpha=0.5, label=name)

    plt.xlabel('Wavelength [{0}]'.format(wavelength_unit))
    plt.ylabel('Filter Response')
    if legend_loc is not None:
        plt.legend(loc = legend_loc)
    plt.grid()
    plt.tight_layout()
    if save is not None:
        plt.savefig(save)
    plt.show()
