#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# demodulate.py
# John A. Marohn
# 2014/06/28 -- 2014/08/07

"""

We demodulate the signal in the following steps:

1. Apply a window to the signal :math:`S(t)` in order to make it
   smoothly ramp up from zero and ramp down to zero.

2. Fast Fourier Transform the windowed signal to obtain
   :math:`\\hat{S}(f)`.

3. Identify the primary oscillation frequency :math:`f_0`.  Apply a
   bandpass filter centered at :math:`f_0` to reject signals at other
   frequencies.

4. Apply a second filter which zeros out the negative-frequency
   components of :math:`\\hat{S}(f)`.

5. Apply an Inverse Fast Fourier Transform to the resulting data to
   obtain a complex signal :math:`z(t) = x(t) + \imath \: y(t)`.

6. Compute the instantaneous phase :math:`\\phi` and amplitude
   :math:`a(t)` using the following equations. Unwrap the phase.

.. math::
    :label: Eq;phi
    
    \\begin{equation}
    \\phi(t) = \\arctan{[\\frac{y(t)}{x(t)}]}
    \\end{equation}

.. math::
    :label: Eq:a
    
    \\begin{equation}
    a(t) = \\sqrt{x(t)^2 + y(t)^2}
    \\end{equation}

7. Calculate the "instantaneous" frequency :math:`f(t)` by dividing the
   instantaneous phase data into equal-time segments and fitting each
   segment to a line.  The average frequency :math:`f(t)` during each time
   segment is the slope of the respective line.

"""

from __future__ import division, print_function, absolute_import
import h5py
import numpy as np 
import scipy as sp 
import math
import time
import datetime
import warnings
from lmfit import minimize, Parameters, fit_report
from freqdemod.hdf5 import update_attrs
from freqdemod.hdf5 import check_minimum_attrs
from freqdemod.hdf5 import infer_missing_attrs
from freqdemod.hdf5 import infer_labels
from freqdemod.hdf5.hdf5_util import save_hdf5, h5ls
print_hdf5_item_structure = h5ls  # Alias for backward compatibility
from freqdemod.util import timestamp_temp_filename
from freqdemod.util import infer_timestep
from collections import OrderedDict
import six
import matplotlib.pyplot as plt 

class Signal(object):

    def __init__(self, filename=None, mode='w-', driver='core', backing_store=False):
        """
        Initialize the *Signal's* hdf5 data structure. Calling with no arguments
        results in an in-memory only object. To save the data to disk, provide
        *both* a filename and ``backing_store=True``.


        :param str filename: the signal's filename.
        :param str mode: file open mode (see h5py.File)
        :param str driver: hdf5 driver (see h5py.File)
        :param bool backing_store: If True, save the file to disk.
        
        Add the following objects to the *Signal* object
        
        :param f: an h5py object stored in core memory 
        :param str report: a string summarizing in words what has
            been done to the signal (e.g., "Empty signal object created")


        Examples::

            s = Signal()                                      # In-memory only
            s = Signal('not-saved.h5')                        # Still in-memory only
            s = Signal('save-to-disk.h5', backing_store=True) # Save to disk
        """
        new_report = []
                
        if filename is not None:
            try:
                self.f = h5py.File(filename, mode=mode, driver=driver, backing_store=backing_store)
            except IOError as e:
                print("IOError: {}".format(e.message))
                if 'file exists' in e.message or 'Unable to open file' in e.message:
                    error_msg = """\
{0} already exists. Fix this error by either:\n
1. Change the filename to a filename that doesn't exist.
2. Remove the file: os.remove('{0}')".format(filename)
3. Explicitly overwrite the file with mode='w'. Run:
    Signal('{0}', mode='w', driver={1}, backing_store={2})""".format(filename, driver, backing_store)
                    raise IOError(error_msg)
                else:
                    raise
            
            
        else:
            filename = timestamp_temp_filename('.h5')
            self.f = h5py.File(filename, mode='w-', driver='core', backing_store=False)

        today = datetime.datetime.today()
        
        attrs = OrderedDict([ \
            ('date',today.strftime("%Y-%m-%d")),
            ('time',today.strftime("%H:%M:%S")),
            ('h5py_version',h5py.__version__),
            ('source','demodulate.py'),
            ('help','Sinusoidally oscillating signal and workup')
            ])
        
        update_attrs(self.f.attrs,attrs)
        new_report.append("HDF5 file {0} created in core memory".format(filename))  
            
        self.report = []
        self.report.append(" ".join(new_report))

    def load_nparray(self, s, s_name, s_unit, dt, s_help='cantilever displacement'):

        """
        Create a *Signal* object from the following inputs.
        
        :param s: the signal *vs* time 
        :type s: np.array  or list
        :param str s_name: the signal's name
        :param str s_name: the signal's units
        :param float dt: the time per point [s]
        :param str s_help: the signal's help string
        
        Add the following objects to the *Signal* object
        
        :param h5py.File f: an h5py object stored in core memory 
        :param str report: a string summarizing in words what has
            been done to the signal 
        
        """
        s = np.atleast_1d(s)

        self.f['x'] = dt * np.arange(s.size)
        attrs = OrderedDict([
            ('name','t'),
            ('unit','s'),
            ('label','t [s]'),
            ('label_latex','$t \: [\mathrm{s}]$'),
            ('help','time'),
            ('initial',0.0),
            ('step',dt)
            ])
        update_attrs(self.f['x'].attrs, attrs)
        
        self.f['y'] = s
        attrs = OrderedDict([
            ('name',s_name),
            ('unit',s_unit),
            ('label','{0} [{1}]'.format(s_name,s_unit)),
            ('label_latex','${0} \: [\mathrm{{{1}}}]$'.format(s_name,s_unit)),
            ('help', s_help),
            ('abscissa','x'),
            ('n_avg',1)
            ])
        update_attrs(self.f['y'].attrs, attrs)   
        
        new_report = []
        new_report.append("Add a signal {0}[{1}]".format(s_name,s_unit))
        new_report.append("of length {0},".format(np.array(s).size))
        new_report.append("time step {0:.3f} us,".format(1E6*dt))
        new_report.append("and duration {0:.3f} s".format(np.array(s).size*dt))
        
        self.report.append(" ".join(new_report))

    def load_hdf5(self, f, s_dataset='y', t_dataset='x', infer_dt=True,
                  infer_attrs=True,):
        """Load an hdf5 file saved with default freqdemod attributes.

        :param f: A filename, or h5py file or group object,
            which contains t_dataset and s_dataset
        :param str s_dataset: signal dataset name (relative to h5object)
        :param str t_dataset: time dataset name (relative to h5object)
        :param bool infer_dt: If True, infer the time step dt from the contents
            of the t_dataset
        :param bool infer_attrs: If True, fill in any missing attributes
            used by freqdemod."""
        if isinstance(f, six.string_types):
            with h5py.File(f, 'r') as fh:
                self._load_hdf5_default(fh, s_dataset=s_dataset,
                                        t_dataset=t_dataset, infer_dt=infer_dt,
                                        infer_attrs=infer_attrs)
        else:
            self._load_hdf5_default(f, s_dataset=s_dataset,
                                    t_dataset=t_dataset,
                                    infer_dt=infer_dt,
                                    infer_attrs=infer_attrs)

    def load_hdf5_general(self, f, s_dataset, s_name, s_unit,
                          t_dataset=None, dt=None,
                          s_help='cantilever displacement'):
        """Load data from an arbitrarily formatted hdf5 file.

        :param f: A filename, or h5py file or group object,
            which contains s_dataset
        :param str s_dataset: signal dataset name (relative to h5object)
        :param str s_name: the signal's name
        :param str s_unit: the signal's units
        :param str t_dataset: time dataset name (optional; or specify dt)
        :param float dt: the time per point [s]
        :param str s_help: the signal's help string
        """
        if isinstance(f, six.string_types):
            with h5py.File(f, 'r') as fh:
                self._load_hdf5_general(fh, s_dataset=s_dataset,
                                        s_name=s_name, s_unit=s_unit,
                                        t_dataset=t_dataset, dt=dt,
                                        s_help=s_help)
        else:
            self._load_hdf5_general(f, s_dataset=s_dataset,
                                    s_name=s_name, s_unit=s_unit,
                                    t_dataset=t_dataset, dt=dt,
                                    s_help=s_help)

    def close(self):
        """Update report, close the file. This will write the file to disk
        if backing_store=True was used to create the file."""

        self.f.attrs['report'] = "\n".join(self.report)
        self.f.close()

    def save(self, dest, save='time_workup', overwrite=False):
        """Save the current signal object to a new hdf5 file, with control over
        what datasets to save.

        :param dest: Copy destination. A filename, or h5py file or group object.
        :param save: A string describing the datasets to be saved, or a list
            of groups / datasets to save.
            all: All datasets and groups
            input: x, y
            input_no_t: y
            time_workup: x, y, workup/time
            time_workup_no_t: y, workup/time
            time_workup_no_s: workup/time
            fit_phase: x, y, workup/fit
            fit_phase_no_t: y, workup/fit
            fit_phase_no_s: workup/fit
        :param overwrite: If true, overwrite an existing destination file.
        """
        self.f.attrs['report'] = "\n".join(self.report)

        save_options = {'all': self.f.keys(),
                        'input': ['x', 'y'],
                        'input_no_t': ['y'],
                        'time_workup': ['x', 'y', 'workup/time'],
                        'time_workup_no_t': ['y', 'workup/time'],
                        'time_workup_no_s': ['workup/time'],
                        'fit_phase':  ['x', 'y', 'workup/fit'],
                        'fit_phase_no_t': ['y', 'workup/fit'],
                        'fit_phase_no_s': ['workup/fit'],
                        }

        if isinstance(save, six.string_types):
            requested_datasets = save_options[save]
            # If save is a string, make sure specified datasets actually exist
            datasets = [dset for dset in requested_datasets if dset in self.f]
            
            if datasets != requested_datasets:
                warnings.warn(
                    "Datasets {} missing, will not be saved.".format(
                    set(requested_datasets) - set(datasets))
                    )
        else:
            # If save is a list, save all datasets in list
            datasets = save

        if isinstance(dest, six.string_types):
            mode = 'w' if overwrite else 'w-'
            f_dst = h5py.File(dest, mode=mode)
        else:
            f_dst = dest

        save_hdf5(self.f, f_dst, datasets)
        # Copy top level attributes over by hand
        update_attrs(f_dst.attrs, self.f.attrs)

        if isinstance(dest, six.string_types):
            f_dst.close()



    def open(self, filename):
        """Open the file for reading and writing."""

        self.f = h5py.File(filename, 'r+')
        self.report = self.f.attrs['report'].split("\n")

    def plot(self, ordinate, LaTeX=False, component='abs'):
        
        """ 
        Plot a component of the *Signal* object.  Both axis are linear.  Display a histogram.
        
            :param str ordinate: the name the y-axis data key
            :param str component: `abs` (default), `real`, `imag`, or `both`;
               if the dataset is complex, which component do we plot 
            :param boolean LaTeX: use LaTeX axis labels; ``True`` or ``False``
                (default)
            
        Plot ``self.f[ordinate]`` versus self.f[y.attrs['abscissa']]
        
        If the data is complex, plot the absolute value.
        
        """

        # Get the x and y axis. 

        y = self.f[ordinate]
        x = self.f[y.attrs['abscissa']]

        # Possibly use tex-formatted axes labels temporarily for this plot
        # and compute plot labels
        
        old_param = plt.rcParams['text.usetex']        
                        
        if LaTeX == True:
        
            plt.rcParams['text.usetex'] = True
            x_label_string = x.attrs['label_latex']
            y_label_string = y.attrs['label_latex']
            
        elif LaTeX == False:
            
            plt.rcParams['text.usetex'] = False
            x_label_string = x.attrs['label']
            y_label_string = y.attrs['label']

        title_string = "{0} vs. {1}".format(y.attrs['help'],x.attrs['help'])

        # Create the plot. If the y-axis is complex, then
        # plot the abs() of it. To use the abs() method, we need to force the 
        # HDF5 dataset to be of type np.ndarray 
        
        # fig=plt.figure(facecolor='w')

        fig, axs = plt.subplots(1, 2, 
                                figsize=(10, 5),
                                width_ratios=[3, 1],
                                sharey=True,
                                tight_layout=True)

        if isinstance(y[0],complex):
            
            if component == 'abs':
                axs[0].plot(x[()], abs(np.array(y[()])))
                yhist = abs(np.array(y[()]))
                y_label_string = "abs of {}".format(y_label_string)
                
            if component == 'real':
                axs[0].plot(x[()], (np.array(y[()])).real)
                yhist = (np.array(y[()])).real
                y_label_string = "real part of {}".format(y_label_string) 
                
            if component == 'imag':
                axs[0].plot(x[()], (np.array(y[()])).imag)
                yhist = (np.array(y[()])).imag
                y_label_string = "imag part of {}".format(y_label_string)
                
            if component == 'both':
                axs[0].plot(x[()], (np.array(y[()])).real)
                axs[0].plot(x[()], (np.array(y[()])).imag)
                yhist = (np.array(y[()])).real # just histogram the real part, for simplicity
                y_label_string = "real and imag part of {}".format(y_label_string)                
                    
        else:

            if isinstance(y[0], (np.bool_, bool)):
                axs[0].plot(x[()], y[()])
                yhist = y[()].astype(int)

            else:
                axs[0].plot(x[()], y[()])
                yhist = y[()]
         
        # axes limits and labels
        
        title_string = "{0} vs. {1}".format(y.attrs['help'], x.attrs['help'])

        axs[0].set_xlabel(x_label_string)
        axs[0].set_ylabel(y_label_string)
        axs[0].set_title(title_string)

        # create a sideways histogram plot with a text
        # message of the mean and stdev

        counts, _ = np.histogram(yhist, bins=256)
        axs[1].hist(yhist, bins=256, orientation="horizontal")   
        axs[1].set_xlabel('counts')

        msg1 = '{:0.3f} {:}'.format(yhist.mean(), y.attrs['unit'])
        msg2 = '{:0.3f} {:}'.format(yhist.std(), y.attrs['unit'])

        axs[1].text(0.90*counts.max(), 1.00*yhist.max(),
                    msg1 + '\n $\pm$ ' + msg2, 
                    fontsize=14, 
                    horizontalalignment='right',
                    verticalalignment='top')

        # set text spacing so that the plot is pleasing to the eye

        plt.locator_params(axis = 'x', nbins = 4)
        plt.locator_params(axis = 'y', nbins = 4)
        fig.subplots_adjust(bottom=0.15,left=0.12)  

        # clean up label spacings, show the plot, and reset the tex option

        # fig.subplots_adjust(bottom=0.15,left=0.12)   
        plt.show()
        plt.rcParams['text.usetex'] = old_param  
        
    def time_mask_binarate(self, mode):
 
        """
        Create a masking array of ``True``/``False`` values that can be used to
        mask an array so that an array is a power of two in length, as required
        to perform a Fast Fourier Transform.  
         
        :param str mode: "start", "middle", or "end" 
        
        With "start", the beginning of the array will be left intact and the 
        end truncated; with "middle", the array will be shortened
        symmetically from both ends; and with "end" the end of the array
        will be left intact while the beginning of the array will be chopped away.
        
        Create the mask in the following ``np.array``:
        
        :param bool self.f['workup/time/mask/binarate']: the mask; a ``np.array`` of boolean values
        
        This boolean array of ``True`` and ``False`` values can be plotted
        directly -- ``True`` is converted to 1.0 and ``False`` is converted to
        0 by the ``matplotlib`` plotting function.
        """       
        
        n = self.f['y'].size     # number of points, n, in the signal
        indices = np.arange(n)   # np.array of indices

        # nearest power of 2 to n
        n2 = int(math.pow(2,int(math.floor(math.log(n, 2)))))
        
        
        if mode == "middle":

            n_start = int(math.floor((n - n2)/2))
            n_stop = int(n_start + n2)
            
        elif mode == "start":
            
            n_start = 0
            n_stop = n2    

        elif mode == "end":

            n_start = n-n2
            n_stop = n            
                                    
        mask = (indices >= n_start) & (indices < n_stop)
        
        dset = self.f.create_dataset('workup/time/mask/binarate',data=mask)            
        attrs = OrderedDict([
            ('name','mask'),
            ('unit','unitless'),
            ('label','masking function'),
            ('label_latex','masking function'),
            ('help','mask to make data a power of two in length'),
            ('abscissa','x')
            ])
        update_attrs(dset.attrs,attrs)      
                           
        x = self.f['x']
        x_binarated = x[mask] 
            
        dset = self.f.create_dataset('workup/time/x_binarated',data=x_binarated)            
        attrs = OrderedDict([
            ('name','t_masked'),
            ('unit','s'),
            ('label','t [s]'),
            ('label_latex','$t \: [\mathrm{s}]$'),
            ('help','time'),
            ('initial', x_binarated[0]),
            ('step', x_binarated[1]-x_binarated[0])
            ])
        update_attrs(dset.attrs,attrs)                
                                    
        new_report = []
        new_report.append("Make an array, workup/time/mask/binarate, to be used")
        new_report.append("to truncate the signal to be {0}".format(n2))
        new_report.append("points long (a power of two). The truncated array")
        new_report.append("will start at point {0}".format(n_start))
        new_report.append("and stop before point {0}.".format(n_stop))
        
        self.report.append(" ".join(new_report))  

    def time_window_cyclicize(self, tw):
        
        """
        Create a windowing function with
        
        :param float tw: the window's target rise/fall time [s] 
        
        The windowing function is a concatenation of
        
        1. the rising half of a Blackman filter;
        
        2. a constant 1.0; and
        
        3. the falling half of a Blackman filter.
        
        The actual rise/fall time may not exactly equal the target rise/fall
        time if the requested time is not an integer multiple of the signal's
        time per point.
        
        If the masking array workup/time/mask/binarate is defined
        then design the windowing function workup/time/window/cyclicize so 
        that is is the right length to be applied to the *masked* signal array.
        If on the other hand the masking array is not already defined, then
        densign the window to be applied to the full signal array instead.
        
        If workup/time/mask/binarate is defined, then also create
        a masked version of the x-axis for plotting, x_binarated.  Make 
        the x_binarated array the new abscissa associated with the new
        workup/time/window/cyclicize array.
        """
        
        if self.f.__contains__('workup/time/mask/binarate') == True:
            
            # You have to cast the HSF5 dataset to a np.array
            # so you can use the array of True/False values as 
            # array indices
            
            m = np.array(self.f['workup/time/mask/binarate'])        
            n = np.count_nonzero(m)
            abscissa = 'workup/time/x_binarated'  
            
        else:
            
            n = self.f['y'].size
            abscissa = 'x'
            
        dt = self.f['x'].attrs['step']          # time per point
        ww = int(math.ceil((1.0*tw)/(1.0*dt)))  # window width (points)
        tw_actual = ww*dt                       # actual window width (seconds)

        w = np.concatenate([np.blackman(2*ww)[0:ww],
                            np.ones(n-2*ww),
                            np.blackman(2*ww)[-ww:]])

        dset = self.f.create_dataset('workup/time/window/cyclicize',data=w)            
        attrs = OrderedDict([
            ('name','window'),
            ('unit','unitless'),
            ('label','windowing function'),
            ('label_latex','windowing function'),
            ('help','window to force the data to start and end at zero'),
            ('abscissa',abscissa),
            ('t_window',tw),
            ('t_window_actual',tw_actual)
            ])
        update_attrs(dset.attrs,attrs)
        
        new_report = []
        new_report.append("Create a windowing function,")
        new_report.append("workup/time/window/cyclicize, with a rising/falling")
        new_report.append("blackman filter having a rise/fall time of")
        new_report.append("{0:.3f} us".format(1E6*tw_actual))
        new_report.append("({0} points).".format(ww))
        
        self.report.append(" ".join(new_report))        
 
    def fft(self, psd=False):

        """
        Take a Fast Fourier transform of the windowed signal. If the signal
        has units of nm, then the FT will have units of nm/Hz.
        
        """
         
        # Start timer 
         
        start = time.time()         
        
        # If a mask is defined then select out a subset of the signal to be FT'ed
        # The signal array, s, should be a factor of two in length at this point        
           
        s = np.array(self.f['y'])   

        if self.f.__contains__('workup/time/mask/binarate') == True:
            
            m = np.array(self.f['workup/time/mask/binarate'])
            s = s[m]

        # If the cyclicizing window is defined then apply it to the signal                
                                                      
        if self.f.__contains__('workup/time/window/cyclicize') == True:
            
            w = np.array(self.f['workup/time/window/cyclicize'])
            s = w*s
          
        # Take the Fourier transform      
                    
        dt = self.f['x'].attrs['step']
          
        freq = np.fft.fftshift(np.fft.fftfreq(s.size,dt))   

        name_orig = self.f['y'].attrs['name']
        unit_orig = self.f['y'].attrs['unit']

        if psd == False:                
            sFT = dt * np.fft.fftshift(np.fft.fft(s))

            sFTunit = '{0}/Hz'.format(unit_orig)
            sFTlabel = 'FT({0}) [{1}]'.format(name_orig,sFTunit)
            sFTlabel_latex = '$\hat{{{0}}} \: [\mathrm{{{1}}}/\mathrm{{Hz}}]$'.format(name_orig,unit_orig)
            sFThelp = 'Fourier transform of {0}(t)'.format(name_orig)

        elif psd == True:
            sFT = (dt / len(s)) * np.power(abs(np.fft.fftshift(np.fft.fft(s))), 2.0)

            sFTunit = '{0}^2/Hz'.format(unit_orig)
            sFTlabel = 'PSD({0}) [{1}^2/Hz]'.format(name_orig,unit_orig)
            sFTlabel_latex = '$P_{{{0}}} \: [\mathrm{{{1}}}^2/\mathrm{{Hz}}]$'.format(name_orig,unit_orig)
            sFThelp = 'Power spectrum of {0}(t)'.format(name_orig)

            # single-sided power s
            mask = freq >= 0
            freq = freq[mask]
            sFT = sFT[mask]

        # Save the data
        
        dset = self.f.create_dataset('workup/freq/freq',data=freq/1E3)
        attrs = OrderedDict([
            ('name','f'),
            ('unit','kHz'),
            ('label','f [kHz]'),
            ('label_latex','$f \: [\mathrm{kHz}]$'),
            ('help','frequency'),
            ('initial',freq[0]),
            ('step',freq[1]-freq[0])
            ])          
        update_attrs(dset.attrs,attrs)        

        dset = self.f.create_dataset('workup/freq/FT',data=sFT)
        attrs = OrderedDict([
            ('name','FT({0})'.format(name_orig)),
            ('unit',sFTunit),
            ('label',sFTlabel),
            ('label_latex',sFTlabel_latex),
            ('help',sFThelp),
            ('abscissa','workup/freq/freq'),
            ('n_avg',1)
            ])
        update_attrs(dset.attrs,attrs)           
           
        # Stop the timer and make a report

        stop = time.time()
        t_calc = stop - start

        new_report = []
        new_report.append("Fourier transform the windowed signal.")
        new_report.append("It took {0:.1f} ms".format(1E3*t_calc))
        new_report.append("to compute the FFT.") 
        self.report.append(" ".join(new_report))

    def freq_filter_Hilbert_complex(self):
        
        """
        Generate the complex Hilbert transform filter (:math:`Hc` in the
        attached notes). Store the filter in::
        
            workup/freq/filter/Hc
            
        The associated filtering function is
        
        .. math::
             
            \\begin{equation}
            \\mathrm{rh}(f) = 
            \\begin{cases}
            0 & \\text{if $f < 0$} \\\\ 
            1 & \\text{if $f = 0$} \\\\ 
            2 & \\text{if $f > 0$}
            \\end{cases}
            \\end{equation}   
        
        """
        
        freq = self.f['workup/freq/freq'][:]
        filt = 0.0*(freq < 0) + 1.0*(freq == 0) + 2.0*(freq > 0)
        
        dset = self.f.create_dataset('workup/freq/filter/Hc',data=filt)            
        attrs = OrderedDict([
            ('name','Hc'),
            ('unit','unitless'),
            ('label','Hc(f)'),
            ('label_latex','$H_c(f)$'),
            ('help','complex Hilbert transform filter'),
            ('abscissa','workup/freq/freq')
            ])
        update_attrs(dset.attrs,attrs)        
        
        new_report = []
        new_report.append("Create the complex Hilbert transform filter.")
        self.report.append(" ".join(new_report))
        
    def freq_filter_bp(self, bw, order=50, style="brick wall"):
        
        """
        Create a bandpass filter with 
        
        :param float bw: filter bandwidth, :math:`\\Delta f` [kHz]
        :param int order: filter order, :math:`n` (defaults to 50)
        :param string style: "brick wall" (default) or "cosine or "gaussian"
        
        Note that the filter width **bw** should be specified **in kHz and
        not Hz**.  Store the filter in::
        
            workup/freq/filter/bp  

        The center frequency, :math:`f_0`, is determined automatically from the positive-frequency
        peak in the Fourier-transform of the cantilever signal.  The filtering function for the
        brick wall filter is
                
        .. math::
             
            \\begin{equation}
            \\mathrm{bp}(f) 
            = \\frac{1}{1 + (\\frac{|f - f_0|}{\\Delta f})^n}
            \\end{equation}
        
        The absolute value makes the filter symmetric, even for odd values of :math:`n`.
        With the **bw** parameter set to 1 kHz, the filter will pass a 2 kHz band of frequencies,
        from 1 kHz below to 1 kHz above the center frequency.  The frequency-noise power spectrum 
        will show noise falling away starting 1 kHz away from the carrier.

        The filtering function for the cosine filter is

        .. math::

            \\begin{equation}
            \\mathrm{bp}(f)
            = \\begin{cases}
            0 & f < f_0 - \\Delta f \\\\
            \\cos{\\left( \\frac{\\pi}{2} \\frac{f-f_0}{\\Delta f} \\right)}
                & f_0 - \\Delta f < f < f_0 + \\Delta f \\\\
            0 & f_0 + \\Delta f < f
            \\end{cases}
            \\end{equation}

        The filtering function for the gaussian filter is

        .. math::

            \\begin{equation}
            \\exp(-(f - f_0)^2/ \\Delta f^2)
            \\end{equation}
                                
        """
        
        # The center frequency fc is the peak in the abs of the FT spectrum
        
        freq = np.array(self.f['workup/freq/freq'][:])
        Hc = np.array(self.f['workup/freq/filter/Hc'][:])
        FTrh = Hc*abs(np.array(self.f['workup/freq/FT'][:]))
        fc = freq[np.argmax(FTrh)]
        
        # Compute the filter
                        
        freq_scaled = (freq - fc)/bw

        if style == "brick wall":

            bp = 1.0/(1.0+np.power(abs(freq_scaled),order))

        elif style == "cosine":

            # here we use a trick

            bp = np.zeros(freq.shape)
            sub_index = (freq >= -1.0*bw + fc) & (freq <= bw + fc)
            sub_indices = np.arange(freq.size)[sub_index]
            bp[sub_indices] = np.sin(np.linspace(0,np.pi,sub_indices.size))

        elif style == "gaussian":

            bp = np.exp(-1 * np.power(freq_scaled, 2.0))

        else:

            print("**ERROR**: Unrecognized filter function")

        dset = self.f.create_dataset('workup/freq/filter/bp',data=bp)            
        attrs = OrderedDict([
            ('name','bp'),
            ('unit','unitless'),
            ('label','bp(f)'),
            ('label_latex','$\mathrm{bp}(f)$'),
            ('help','bandpass filter'),
            ('abscissa','workup/freq/freq')
            ])
        update_attrs(dset.attrs,attrs)          
        
        # For fun, make an improved estimate of the center frequency
        #  using the method of moments -- this only gives the right answer
        #  because we have applied the nice bandpass filter first
        
        FT_filt = Hc*bp*abs(self.f['workup/freq/FT'][:])
        fc_improved = (freq*FT_filt).sum()/FT_filt.sum()
        
        new_report = []
        new_report.append("Create a bandpass filter with center frequency")
        new_report.append("= {0:.6f} kHz,".format(fc))
        new_report.append("bandwidth = {0:.3f} kHz,".format(bw))
        new_report.append("and order = {0}.".format(order))
        new_report.append("Best estimate of the resonance")
        new_report.append("frequency = {0:.6f} kHz.".format(fc_improved))
                
        self.report.append(" ".join(new_report))
        
    def time_mask_rippleless(self, td): 
        
        """
        Defined using
        
        :param float td: the dead time [s] 

        that will remove the leading and trailing rippled from the phase (and
        amplitude) versus time data.

        **Programming notes**.  As a result of the applying cyclicizing window
        in the time domain and the bandpass filter in the frequency domain,
        the phase (and amplitude) will have a ripple at its leading and trailing
        edge.  We want to define a mask that will let us focus on the 
        uncorrupted data in the middle of the phase (and amplitude) array
        
        Defining the required mask takes a little thought.We need to first
        think about what the relevant time xis is.  If::
        
            self.f.__contains__('workup/time/mask/binarate') == True:
            
        then the data has been trimmed to a factor of two and the relevant 
        time axis is::
            
            self.f['/workup/time/x_binarated']
            
        Otherwise, the relevant time axis is::
        
            self.f['x'] 
        
        We will call this trimming mask::
        
            self.f['workup/time/mask/rippleless']
        
        We will apply this mask to either ``self.f['/workup/time/x_binarated']``
        or ``self.f['x']`` to yield a new time array for plotting phase (and 
        amplitude) data::
        
            self.f['/workup/time/x_rippleless']
            
        If no bandpass filter was defined, then the relevant time axis for the 
        phase (and amplitude) data is either::
        
            self.f['/workup/time/x_binarated']
            (if self.f.__contains__('workup/time/mask/binarate') == True)    
        
        or::
        
            self.f['x']
            (if self.f.__contains__('workup/time/mask/binarate') == False) 
        
        """        

        dt = self.f['x'].attrs['step']          # time per point
        ww = int(math.ceil((1.0*td)/(1.0*dt)))  # window width (points)
        td_actual = ww*dt                       # actual dead time (seconds)        
           
        if self.f.__contains__('workup/time/mask/binarate') == True:
            x = np.array(self.f['/workup/time/x_binarated'][:])
            abscissa = '/workup/time/x_binarated'

        else:
            x = np.array(self.f['x'][:])
            abscissa = 'x'
            
        n = x.size
        indices = np.arange(n)
        mask = (indices >= ww) & (indices < n - ww)
        x_rippleless = x[mask]
        
        dset = self.f.create_dataset('workup/time/mask/rippleless',data=mask)            
        attrs = OrderedDict([
            ('name','mask'),
            ('unit','unitless'),
            ('label','masking function'),
            ('label_latex','masking function'),
            ('help','mask to remove leading and trailing ripple'),
            ('abscissa',abscissa)
            ])
        update_attrs(dset.attrs,attrs)
        
        dset = self.f.create_dataset('workup/time/x_rippleless',data=x_rippleless)            
        attrs = OrderedDict([
            ('name','t_masked'),
            ('unit','s'),
            ('label','t [s]'),
            ('label_latex','$t \: [\mathrm{s}]$'),
            ('help','time'),
            ('initial', x_rippleless[0]),
            ('step', x_rippleless[1]-x_rippleless[0])
            ])
        update_attrs(dset.attrs,attrs)              
                        
        new_report = []
        new_report.append("Make an array, workup/time/mask/rippleless, to be")
        new_report.append("used to remove leading and trailing ripple.  The")
        new_report.append("dead time is {0:.3f} us.".format(1E6*td_actual))
        
        self.report.append(" ".join(new_report))          
        
    def ifft(self):
        
        """
        If they are defined, 
        
        * apply the complex Hilbert transform filter,
        
        * apply the bandpass filter,
        
        then 
        
        * compute the inverse Fourier transform,
        
        * if a trimming window is defined then trim the result
         
        
        """
        
        # Divide the FT-ed data by the timestep to recover the 
        # digital Fourier transformed data.  Carry out the 
        # transforms.
        
        s = self.f['workup/freq/FT'][:]/self.f['x'].attrs['step']

        if self.f.__contains__('workup/freq/filter/Hc') == True:
            s = s*self.f['workup/freq/filter/Hc']            
                                    
        if self.f.__contains__('workup/freq/filter/bp') == True:
            s = s*self.f['workup/freq/filter/bp']
            
        # Compute the IFT    
            
        sIFT = np.fft.ifft(np.fft.ifftshift(s))
        
        # Trim if a rippleless masking array is defined
        # Carefullly define what we should plot the complex
        # FT-ed data against.
        
        if self.f.__contains__('workup/time/mask/rippleless') == True:
            
            mask = np.array(self.f['workup/time/mask/rippleless'])
            sIFT = sIFT[mask]
            abscissa = 'workup/time/x_rippleless'
            
        else:
            
            if self.f.__contains__('workup/time/mask/binarate') == True:
                abscissa = '/workup/time/x_binarated'
            else:
                abscissa = 'x'
        
        dset = self.f.create_dataset('workup/time/z',data=sIFT)
        unit_y = self.f['y'].attrs['unit']
        attrs = OrderedDict([
            ('name','z'),
            ('unit',unit_y),
            ('label','z [{0}]'.format(unit_y)),
            ('label_latex','$z \: [\mathrm{{{0}}}]$'.format(unit_y)),
            ('help','complex cantilever displacement'),
            ('abscissa',abscissa)
            ])
        update_attrs(dset.attrs,attrs)         

        # Compute and save the phase and amplitude
        
        p = np.unwrap(np.angle(sIFT))/(2*np.pi)
        dset = self.f.create_dataset('workup/time/p',data=p)
        attrs = OrderedDict([
            ('name','phase'),
            ('unit','cyc'),
            ('label','phase [cyc]'),
            ('label_latex','$\phi \: [\mathrm{cyc}]$'),
            ('help','cantilever phase'),
            ('abscissa',abscissa)
            ])
        update_attrs(dset.attrs,attrs)
                  
        a = abs(sIFT)
        dset = self.f.create_dataset('workup/time/a',data=a)
        attrs = OrderedDict([
            ('name','amplitude'),
            ('unit',unit_y),
            ('label','a [{0}]'.format(unit_y)),
            ('label_latex','$a \: [\mathrm{{{0}}}]$'.format(unit_y)),
            ('help','cantilever amplitude'),
            ('abscissa',abscissa)
            ])
        update_attrs(dset.attrs,attrs)
          
        new_report = []
        new_report.append("Apply an inverse Fourier transform.")
        self.report.append(" ".join(new_report))
        
    def fit_phase(self, dt_chunk_target):
        
        """
        Fit the phase *vs* time data to a line.  The slope of the line is the
        (instantaneous) frequency. The phase data is broken into "chunks", with
        
        :param float dt_chunk_target: the target chunk duration [s]
        
        If the chosen duration is not an integer multiple of the digitization
        time, then find the nearest chunk duration which is.
        
        Calculate the slope :math:`m` of the phase *vs* time line using
        the linear-least squares formula
        
        .. math::
            
            \\begin{equation}
            m = \\frac{n \\: S_{xy} - S_x S_y}{n \\: S_{xx} - (S_x)^2}
            \\end{equation}
        
        with :math:`x` representing time, :math:`y` representing
        phase, and :math:`n` the number of data points contributing to the 
        fit.  The sums involving the :math:`x` (e.g., time) data can be computed
        analytically because the time data here are equally spaced.  With the 
        time per point :math:`\\Delta t`, 
        
        .. math::
            
            \\begin{equation}
            S_x = \\sum_{k = 0}^{n-1} x_k 
            = \\sum_{k = 0}^{n-1} k \\: \\Delta t
             = \\frac{1}{2} \\Delta t \\: n (n-1)
            \\end{equation}
            
            \\begin{equation}
            S_{xx} = \\sum_{k = 0}^{n-1} x_k^2 
            = \\sum_{k = 0}^{n-1} k^2 \\: {\\Delta t}^2
             = \\frac{1}{6} \\Delta t \\: n (n-1) (2n -1)
            \\end{equation}
        
        The sums involving :math:`y` (e.g., phase) can not be similarly
        precomputed. These sums are

        .. math:: 
                       
             \\begin{equation}
             S_y =  \\sum_{k = 0}^{n-1} y_k = \\sum_{k = 0}^{n-1} \\phi_k
             \\end{equation} 

             \\begin{equation}
             S_{xy} =  \\sum_{k = 0}^{n-1} x_k y_k 
             = \\sum_{k = 0}^{n-1} (k \\Delta t) \\: \\phi_k
             \\end{equation}         
        
        To avoid problems with round-off error, a constant is subtracted from 
        the time and phase arrays in each chuck so that the time array
        and phase array passed to the least-square formula each start at
        zero.
        
        """                        

        # work out the chunking details

        dt = self.f['x'].attrs['step']                # time per phase point
        n = np.array(self.f['workup/time/p'][:]).size # no. of phase points
        
        n_per_chunk = int(round(dt_chunk_target/dt)) # points per chunck
        dt_chunk = dt*n_per_chunk                    # actual time per chunk
        n_tot_chunk = int(n/n_per_chunk)             # total number of chunks
        n_total = n_per_chunk*n_tot_chunk            # (realizable) no. of phase points
        
        # report the chunking details
        
        new_report = []
        new_report.append("Curve fit the phase data.")
        new_report.append("The target chunk duration is")
        new_report.append("{0:.3f} us;".format(1E6*dt_chunk_target))
        new_report.append("the actual chunk duration is")
        new_report.append("{0:.3f} us".format(1E6*dt_chunk))
        new_report.append("({0} points).".format(n_per_chunk))
        new_report.append("The associated Nyquist")
        new_report.append("frequency is {0:.3f} kHz.".format(1/(2*1E3*dt_chunk)))
        new_report.append("A total of {0} chunks will be curve fit,".format(n_tot_chunk))
        new_report.append("corresponding to {0:.3f} ms of data.".format(1E3*dt*n_total))
        
        self.report.append(" ".join(new_report))
        start = time.time() 
        
        # Reshape the phase data and 
        #  zero the phase at start of each chunk
        
        y = np.array(self.f['workup/time/p'][0:n_total])        
        y_sub = y.reshape((n_tot_chunk,n_per_chunk))
        y_sub_reset = y_sub - y_sub[:,:,np.newaxis][:,0,:]*np.ones(n_per_chunk)

        # Reshape the time data
        #  zero the time at start of each chunk

        abscissa = self.f['workup/time/p'].attrs['abscissa']
        x = np.array(self.f[abscissa])[0:n_total]
        x_sub = x.reshape((n_tot_chunk,n_per_chunk))
        x_sub_reset = x_sub - x_sub[:,:,np.newaxis][:,0,:]*np.ones(n_per_chunk)

        # use linear least-squares fitting formulas
        #  to calculate the best-fit slope

        SX = dt*0.50*(n_per_chunk-1)*(n_per_chunk)
        SXX = (dt)**2*(1/6.0)*(n_per_chunk)*(n_per_chunk-1)*(2*n_per_chunk-1)
        SY = np.sum(y_sub_reset,axis=1)
        SXY = np.sum(x_sub_reset*y_sub_reset,axis=1)
        slope = (n_per_chunk*SXY-SX*SY)/(n_per_chunk*SXX-SX*SX)

        stop = time.time()
        t_calc = stop - start
                                
        # Save the time axis and the slope
        #
        # Tricky: the time we want is the time in the ~middle~ of each chunk
        #
        #     old: x_sub[:,0]
        #     new: x_sub_middle = np.mean(x_sub[:,:],axis=1)

        x_sub_middle = np.mean(x_sub[:,:],axis=1)
        dset = self.f.create_dataset('workup/fit/x',data=x_sub_middle)
        attrs = OrderedDict([
            ('name','t'),
            ('unit','s'),
            ('label','t [s]'),
            ('label_latex','$t \: [\mathrm{s}]$'),
            ('help','time at the start of each chunk')
            ])
        update_attrs(dset.attrs,attrs)
                  
        dset = self.f.create_dataset('workup/fit/y',data=slope)
        attrs = OrderedDict([
            ('name','f'),
            ('unit','cyc/s'),
            ('label','f [cyc/s]'),
            ('label_latex','$f \: [\mathrm{cyc/s}]$'),
            ('help','best-fit slope'),
            ('abscissa','workup/fit/x')
            ])
        update_attrs(dset.attrs,attrs)        
        
        # report the curve-fitting details
        #  and prepare the report
        
        new_report.append("It took {0:.1f} ms".format(1E3*t_calc))
        new_report.append("to perform the curve fit and obtain the frequency.")
                 
        self.report.append(" ".join(new_report))      

    def fit_amplitude(self):
        
        """
        Fit the data stored at::
        
            workup/time/a
        
        To a decaying exponential.  Store the resulting fit at::
        
            workup/fit/exp
        
        **Programming Notes**
        
        * From the ``lmfit`` documentation [`link <http://newville.github.io/lmfit-py/fitting.html#fit_report>`__]:    
          "Note that the calculation of chi-square and reduced chi-square assume
          that the returned residual function is scaled properly to the
          uncertainties in the data. For these statistics to be meaningful,
          the person writing the function to be minimized must scale 
          them properly."  
            
            
        """
        
        # extract the data from the Datasets
        y_dset = self.f['workup/time/a']
        x = np.array(self.f[y_dset.attrs['abscissa']][:])
        y = np.array(y_dset[:])
        
        # define objective function: returns the array to be minimized
        def fcn2min(params, x, y, y_stdev):
            """ model decaying exponentil, subtract data"""
            a0 = params['a0'].value
            a1 = params['a1'].value
            tau = params['tau'].value
            y_calc = a0*np.exp(-x/tau) + a1
            return (y_calc - y)/y_stdev

        # create a set of Parameters
        params = Parameters()
        params.add('a0', value= 1.0,  min=0)
        params.add('a1', value= 0.1,  min=0)
        params.add('tau', value= 1.0)

        # do fit once, here with leastsq model
        y_stdev = np.ones(y.size)
        result = minimize(fcn2min, params, args=(x,y,y_stdev))

        # do fit again, using the standard deviation of the residuals
        #  as an estimate of the standard error in each data point
        y_stdev = np.std(result.residual)*np.ones(y.size)
        result = minimize(fcn2min, params, args=(x,y,y_stdev))

        # calculate final result
        y_calc = y + y_stdev*result.residual

        # write error report
        rep = fit_report(params)

        # store fit results!
        # format string examples: http://mkaz.com/2012/10/10/python-string-format/

        dset = self.f.create_group('workup/fit/exp')
        
        p = result.params

        title = "a(t) = a0*exp(-t/tau) + a1" \
                "\n" \
                "a0 = {0:.6f} +/- {1:.6f}, tau = {2:.6f} +/- {3:.6f}, a1 = {4:.6f} +/- {5:.6f}".\
                format(p['a0'].value, p['a0'].stderr,
                        p['tau'].value, p['tau'].stderr,
                        p['a1'].value, p['a1'].stderr)
                   
        title_LaTeX = r'$a(t) = a_0 \exp(-t/\tau) + a_1$' \
                     '\n' \
                     r'$a_0 = {0:.6f} \pm {1:.6f}, \tau = {2:.6f} \pm {3:.6f}, a_1 = {4:.6f} \pm {5:.6f}$'. \
                    format(p['a0'].value, p['a0'].stderr,
                        p['tau'].value, p['tau'].stderr,
                        p['a1'].value, p['a1'].stderr)
                
        attrs = OrderedDict([
            ('abscissa', y_dset.attrs['abscissa']),
            ('ordinate', 'workup/time/a'),
            ('fit_report', rep),
            ('help', 'fit to decaying exponential'),
            ('title', title),
            ('title_LaTeX', title_LaTeX),
            ('tau', p['tau'].value),
            ('tau_stderr', p['tau'].stderr),
            ('a0', p['a0'].value),
            ('a0_stderr', p['a0'].stderr), 
            ('a1', p['a1'].value),
            ('a1_stderr', p['a1'].stderr)         
            ])
        update_attrs(dset.attrs,attrs)
        
        dset = self.f.create_dataset('workup/fit/exp/y_calc',data=y_calc)
        a_unit = self.f['workup/time/a'].attrs['unit']
        attrs = OrderedDict([
            ('abscissa', y_dset.attrs['abscissa']), 
            ('name', 'a (calc)'),
            ('unit', a_unit),
            ('label', 'a (calc) [{0}]'.format(a_unit)),
            ('label_latex', '$a_{{\mathrm{{calc}}}} \: [\mathrm{{{0}}}]$'.format(a_unit)),
            ('help', 'cantilever amplitude (calculated)')
            ])
        update_attrs(dset.attrs,attrs)
                
        dset = self.f.create_dataset('workup/fit/exp/y_resid',data=result.residual) 
        attrs = OrderedDict([ 
            ('abscissa', y_dset.attrs['abscissa']),
            ('name', 'a (resid)'),
            ('unit', a_unit),
            ('label', 'a (resid) [{0}]'.format(a_unit)),
            ('label_latex', '$a - a_{{\mathrm{{calc}}}} \: [\mathrm{{{0}}}]$'.format(a_unit)),
            ('help', 'cantilever amplitude (residual)')
            ])
        update_attrs(dset.attrs,attrs)

    def plot_fit(self, fit_group, LaTeX=False):
        
        """
        Plot the fit stored in ``fit_group``. 
        """
        
        # helpful
        # http://stackoverflow.com/questions/4209467/matplotlib-share-x-axis-but-dont-show-x-axis-tick-labels-for-both-just-one
        
        y_dset = self.f[fit_group].attrs['ordinate']
        x_dset = self.f[fit_group].attrs['abscissa']
        y_calc_dset = fit_group + '/y_calc'
        y_resid_dset = fit_group + '/y_resid'

        # Possibly use tex-formatted axes labels temporarily for this plot
        # and compute plot labels
        
        old_param = plt.rcParams['text.usetex']        
                        
        if LaTeX == True:
        
            plt.rcParams['text.usetex'] = True
            x_label_string = self.f[x_dset].attrs['label_latex']
            y_label_string = self.f[y_dset].attrs['label_latex']
            y2_label_string = self.f[y_resid_dset].attrs['label_latex']
            title_string = self.f[fit_group].attrs['title_LaTeX']
            
        elif LaTeX == False:
            
            plt.rcParams['text.usetex'] = False
            x_label_string = self.f[x_dset].attrs['label']
            y_label_string = self.f[y_dset].attrs['label']
            y2_label_string = self.f[y_resid_dset].attrs['label']
            title_string = self.f[fit_group].attrs['title']

        y = np.array(self.f[y_dset][:])
        y_calc = np.array(self.f[y_calc_dset][:])
        y_resid = np.array(self.f[y_resid_dset][:])
        x = np.array(self.f[x_dset][:])

        fig=plt.figure(facecolor='w')                
        
        ax1 = fig.add_subplot(211)
        ax1.plot(x,y,'k.')
        ax1.plot(x,y_calc,'r')
        plt.ylabel(y_label_string)
        plt.title(title_string, fontsize=16)  
        plt.setp(ax1.get_xticklabels(), visible=False)
        
        ax2 = fig.add_subplot(212,sharex=ax1)
        ax2.plot(x,y_resid,'k.') 
        plt.xlabel(x_label_string)
        plt.ylabel(y2_label_string)      

       # set text spacing so that the plot is pleasing to the eye

        plt.locator_params(axis = 'x', nbins = 4)
        plt.locator_params(axis = 'y', nbins = 4)
        fig.subplots_adjust(bottom=0.15,left=0.12)  

        # clean up label spacings, show the plot, ... and reset the tex option
          
        fig.subplots_adjust(bottom=0.15,left=0.12) 
        plt.show()
        plt.rcParams['text.usetex'] = old_param  
        
    def __repr__(self):

        """
        Print out the report.
        
        """

        temp = []
        temp.append("")
        temp.append("Signal report")
        temp.append("=============")
        temp.append("\n\n".join(["* " + msg for msg in self.report]))

        return '\n'.join(temp)

    def list(self, offset='', indent ='    '):

        """
        List all file/group/dataset elements in the hdf5 file by iterating
        over the file contents.

        Source::

           https://confluence.slac.stanford.edu/display/PSDM
           /How+to+access+HDF5+data+from+Python
           #HowtoaccessHDF5datafromPython-HDF5filestructure

        """

        print("")
        print("Signal file summary")
        print("===================")
        h5ls(self.f)

    def _load_hdf5_default(self, h5object, s_dataset='y', t_dataset='x',
                           infer_dt=True, infer_attrs=True):
        """Load an hdf5 file saved with default freqdemod attributes.

        :param h5object: An h5py File or group object, which contains t_dataset
            and s_dataset
        :param str s_dataset: signal dataset name (relative to h5object)
        :param str t_dataset: time dataset name (relative to h5object)
        :param bool infer_dt: If True, infer the time step dt from the contents
            of the t_dataset
        :param bool infer_attrs: If True, fill in any missing attributes
            used by freqdemod."""
        h5object.copy(t_dataset, self.f, name='x')
        h5object.copy(s_dataset, self.f, name='y')

        x_attrs = self.f['x'].attrs
        y_attrs = self.f['y'].attrs

        if infer_dt:
            check_minimum_attrs(x_attrs, 'permissive')
            x_attrs['step'] = infer_timestep(h5object[t_dataset])

        if infer_attrs:
            check_minimum_attrs(x_attrs, 'permissive_x')
            infer_missing_attrs(x_attrs, dataset_type='x')

            check_minimum_attrs(y_attrs, 'permissive')
            infer_missing_attrs(y_attrs, dataset_type='y', abscissa='x')

        check_minimum_attrs(x_attrs, 'freqdemod_x')
        check_minimum_attrs(y_attrs, 'freqdemod_y')

    def _load_hdf5_general(self, h5object, s_dataset, s_name, s_unit,
                           t_dataset=None, dt=None,
                           s_help='cantilever displacement'):
        """Load data from an arbitrarily formatted hdf5 file.

        :param h5object: An h5py File or group object, which contains s_dataset
        :param str s_dataset: signal dataset name (relative to h5object)
        :param str s_name: the signal's name
        :param str s_name: the signal's units
        :param str t_dataset: time dataset name (optional; or specify dt)
        :param float dt: the time per point [s]
        :param str s_help: the signal's help string
        """
        h5object.copy(s_dataset, self.f, name='y', without_attrs=True)
        y_attrs = {'name': s_name,
                   'unit': s_unit,
                   'help': s_help,
                   'abscissa': 'x',
                   'n_avg': 1}
        update_attrs(self.f['y'].attrs, y_attrs)
        infer_labels(self.f['y'].attrs)

        if t_dataset is not None:
            h5object.copy(t_dataset, self.f, name='x', without_attrs=True)
            dt_ = infer_timestep(h5object[t_dataset])
        elif dt is not None:
            self.f['x'] = dt * np.arange(self.f['y'][:].size)
            dt_ = dt
        else:
            raise ValueError("Must specify one of 't_dataset' or 'dt'")

        x_attrs = {'name': 't',
                   'unit': 's',
                   'label': 't [s]',
                   'label_latex': '$t \: [\mathrm{s}]$',
                   'help': 'time',
                   'initial': 0.0,
                   'step': dt_}

        update_attrs(self.f['x'].attrs, x_attrs)

def testsignal_sine():
        
    fd = 50.0E3    # digitization frequency
    f0 = 2.00E3    # signal frequency
    nt = 60E3      # number of signal points    
    sn = 1.0       # signal zero-to-peak amplitude
    sn_rms = 0.01  # noise rms amplitude
    
    dt = 1/fd
    t = dt*np.arange(nt)
    s = sn*np.sin(2*np.pi*f0*t) + np.random.normal(0,sn_rms,t.size)
    
    S = Signal()
    S.load_nparray(s,"x","nm",dt)

    S.time_mask_binarate("middle")
    S.time_window_cyclicize(3E-3)
    S.fft()
    S.freq_filter_Hilbert_complex()
    S.freq_filter_bp(bw=1.00, style="cosine")
    S.time_mask_rippleless(15E-3)
    S.ifft()
    S.fit_phase(221.34E-6)
        
    S.plot('y', LaTeX=latex)
    S.plot('workup/time/mask/binarate', LaTeX=latex)
    S.plot('workup/time/window/cyclicize', LaTeX=latex)
    S.plot('workup/freq/FT', LaTeX=latex, component='abs')
    S.plot('workup/freq/filter/Hc', LaTeX=latex)
    S.plot('workup/freq/filter/bp', LaTeX=latex)
    S.plot('workup/time/mask/rippleless', LaTeX=latex)
    S.plot('workup/time/z', LaTeX=latex, component='both')
    S.plot('workup/time/a', LaTeX=latex)
    S.plot('workup/time/p', LaTeX=latex)
    S.plot('workup/fit/y', LaTeX=latex)
                           
    print(S)
    S.list()
    return S

def testsignal_sine_fm():
    
    fd = 100E3         # digitization frequency
    f_start = 4.000E3  # starting frequency
    f_end = 6.000E3    # ending frequency
            
    # time array

    dt = 1/fd    
    t1 = dt*np.arange(int(round(0.25/dt)))
    t2 = dt*np.arange(int(round((0.75-0.25)/dt)))
    t3 = dt*np.arange(128*1024-t1.size-t2.size)
    
    t2plus = t2+t1[-1]+dt
    t3plus = t3+t2plus[-1]+dt
    
    t = np.append(np.append(t1,t2plus),t3plus)
    
    # frequency array
    
    f1 = f_start*np.ones(t1.size)
    f2 = f_start + t2*(f_end-f_start)/(t2[-1]-t2[0])
    f3 = f_end*np.ones(t3.size)
    
    f = np.append(np.append(f1,f2),f3)
    
    # phase accumulator
    
    p = np.zeros(t.size)
    p[0] = 0.0
    
    for k in np.arange(1,t.size):
        p[k] = p[k-1] + dt*f[k-1]
    
    p = 2*np.pi*p
    x = np.cos(p)

    # make the single and work it up

    S = Signal()
    S.load_nparray(x,"x","nm",dt)

    S.time_mask_binarate("middle")
    S.time_window_cyclicize(3E-3)
    S.fft()
    S.freq_filter_Hilbert_complex()
    S.freq_filter_bp(4.00)
    S.time_mask_rippleless(15E-3)
    S.ifft()
    S.fit_phase(200E-6)
        
    S.plot('y', LaTeX=latex)
    S.plot('workup/freq/FT', LaTeX=latex, component='abs')
    S.plot('workup/freq/filter/bp', LaTeX=latex)
    S.plot('workup/time/z', LaTeX=latex, component='both')
    S.plot('workup/time/p', LaTeX=latex)
    S.plot('workup/fit/y', LaTeX=latex)
                           
    print(S)
    return S

def testsignal_sine_exp():
    
    fd = 50.0E3    # digitization frequency [Hz]
    f0 = 2.00E3    # signal frequency [Hz]
    tau = 0.325    # decay time [s]
    nt = 2.00*fd   # number of signal points (before truncation)    
    sn = 100.0     # signal zero-to-peak amplitude [nm]
    sn_rms = 20.0   # noise rms amplitude [nm]
    
    dt = 1/fd
    t = dt*np.arange(nt)
    s = sn*np.sin(2*np.pi*f0*t)*np.exp(-t/tau) + np.random.normal(0,sn_rms,t.size)
    
    S = Signal('.temp_sine_exp.h5')
    S.load_nparray(s,"x","nm",dt)

    S.time_mask_binarate("start")
    S.fft()
    S.freq_filter_Hilbert_complex()
    
    S.freq_filter_bp(1.00)            # 2.00 kHz => 1.0 ms filter timescale
    S.time_mask_rippleless(10E-3)      # 10 x the filter timescale
    S.ifft()
        
    S.plot('y', LaTeX=latex)
    S.plot('workup/freq/FT', LaTeX=latex, component='abs')
    S.plot('workup/freq/filter/bp', LaTeX=latex)
    S.plot('workup/time/a', LaTeX=latex)
    
    S.fit_amplitude()
    S.plot_fit('/workup/fit/exp', LaTeX=latex)
                           
    print(S)
    return S
    
def testsignal_sine_noise():

    fd = 50.0E3    # digitization frequency
    f0 = 2.00E3    # signal frequency
    tau = 0.325    # decay time [s]
    nt = 60E3      # number of signal points    
    sn = 1.0       # signal zero-to-peak amplitude
    sn_rms = 0.1   # noise rms amplitude
    
    dt = 1/fd
    t = dt*np.arange(nt)
    s = sn*np.sin(2*np.pi*f0*t)*np.exp(-t/tau) + np.random.normal(0,sn_rms,t.size)
    
    S = Signal()
    S.load_nparray(s,"x","nm",dt)

    S.time_mask_binarate("middle")
    S.time_window_cyclicize(3E-3)
    S.fft(psd=True)
        
    S.plot('y', LaTeX=latex)
    S.plot('workup/freq/FT', LaTeX=latex)
                           
    print(S)
    S.list()
    return S

if __name__ == "__main__":
    
    # Parge command-line arguments
    # https://docs.python.org/2/library/argparse.html#module-argparse
    
    import argparse
    from argparse import RawTextHelpFormatter
    
    parser = argparse.ArgumentParser(formatter_class=RawTextHelpFormatter,
        description="Determine a signal's frequency vs time.\n"
        "Example usage:\n"
        "    python demodulate.py --testsignal=sine --LaTeX\n\n")
    parser.add_argument('--testsignal',
        default='sine',
        choices = ['sine', 'sinefm', 'sineexp', 'sinenoise'],
        help='create and analyze a test signal')
    parser.add_argument('--LaTeX',
        dest='latex',
        action='store_true',
        help = 'use LaTeX plot labels')
    parser.add_argument('--no-LaTeX',
        dest='latex',
        action='store_false',
        help = 'do not use LaTeX plot labels (default)')
    parser.set_defaults(latex=False)    
    args = parser.parse_args()
    
    # Set the default font and size for the figure
    
    font = {'family' : 'serif',
            'weight' : 'normal',
            'size'   : 18}

    plt.rc('font', **font)
    plt.rcParams['figure.figsize'] =  8.31,5.32
    
    latex = args.latex
    
    # Do one of the tests
    
    if args.testsignal == 'sine': 
        S = testsignal_sine()

    elif args.testsignal == 'sinefm': 
        S = testsignal_sine_fm()        

    elif args.testsignal == 'sineexp': 
        S = testsignal_sine_exp()                            

    elif args.testsignal == 'sinenoise': 
        S = testsignal_sine_noise()    

    else:
        print("**warning **")
        print("--testsignal={} not implimented yet".format(args.testsignal))
