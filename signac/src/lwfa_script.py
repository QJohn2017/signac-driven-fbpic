"""
This is a typical input script that runs a simulation of
laser-wakefield acceleration using FBPIC.

Usage
-----
- Modify the parameters below to suit your needs
- Type "python lwfa_script.py" in a terminal

Help
----
All the structures implemented in FBPIC are internally documented.
Enter "print(fbpic_object.__doc__)" to have access to this documentation,
where fbpic_object is any of the objects or function of FBPIC.
"""

# -------
# Imports
# -------
import numpy as np
from fbpic.lpa_utils.laser import add_laser_pulse, GaussianLaser
# Import the relevant structures in FBPIC
from fbpic.main import Simulation
from fbpic.openpmd_diag import FieldDiagnostic, ParticleDiagnostic, \
    set_periodic_checkpoint, restart_from_checkpoint
from matplotlib import pyplot
from scipy.constants import c, e, m_e
from sliceplots import Plot2D

# ----------
# Parameters
# ----------


# Whether to use the GPU
use_cuda = True

# Order of the stencil for z derivatives in the Maxwell solver.
# Use -1 for infinite order, i.e. for exact dispersion relation in
# all direction (adviced for single-GPU/single-CPU simulation).
# Use a positive number (and multiple of 2) for a finite-order stencil
# (required for multi-GPU/multi-CPU with MPI). A large `n_order` leads
# to more overhead in MPI communications, but also to a more accurate
# dispersion relation for electromagnetic waves. (Typically,
# `n_order = 32` is a good trade-off.)
# See https://arxiv.org/abs/1611.05712 for more information.
n_order = -1

# The simulation box
Nz = 4096         # Number of gridpoints along z
zmax = 30.e-6    # Right end of the simulation box (meters)
zmin = -70.e-6   # Left end of the simulation box (meters)
Nr = 256          # Number of gridpoints along r
rmax = 30.e-6    # Length of the box along r (meters)
Nm = 2           # Number of modes used

# The simulation timestep
dt = (zmax-zmin)/Nz/c   # Timestep (seconds)

# The particles
p_zmin = 0.e-6  # Position of the beginning of the plasma (meters)
p_zmax = 2250.e-6 # Position of the end of the plasma (meters)
p_rmax = 27.e-6  # Maximal radial position of the plasma (meters)
n_e = 7.5e18*1.e6 # Density (electrons.meters^-3)
p_nz = 2         # Number of particles per cell along z
p_nr = 2         # Number of particles per cell along r
p_nt = 4         # Number of particles per cell along theta

# The laser
a0 = 4.          # Laser amplitude
w0 = 9.e-6       # Laser waist
ctau = 9.e-6     # Laser duration
z0 = 0.e-6      # Laser centroid
lambda0 = 0.8e-6  # Laser wavelength (meters)

# The moving window
v_window = c       # Speed of the window

# The diagnostics and the checkpoints/restarts
diag_period = 100         # Period of the diagnostics in number of timesteps
save_checkpoints = False # Whether to write checkpoint files
checkpoint_period = 200  # Period for writing the checkpoints
use_restart = False      # Whether to restart from a previous checkpoint
track_electrons = True  # Whether to track and write particle ids

# The density profile
ramp_start = 0.e-6
ramp_length = 375.e-6


def dens_func( z, r ) :
    """Returns relative density at position z and r"""
    # Allocate relative density
    n = np.ones_like(z)
    # Make linear ramp
    n = np.where( z<ramp_start+ramp_length, (z-ramp_start)/ramp_length, n )
    # Supress density before the ramp
    n = np.where( z<ramp_start, 0., n )
    return(n)


# The interaction length of the simulation (meters)
L_interact = 900.e-6 - (zmax - zmin)  # increase to simulate longer distance!

# Interaction time (seconds) (to calculate number of PIC iterations)
T_interact = ( L_interact + (zmax-zmin) ) / v_window
# (i.e. the time it takes for the moving window to slide across the plasma)

# ---------------------------
# Carrying out the simulation
# ---------------------------

# NB: The code below is only executed when running the script,
# (`python lwfa_script.py`), but not when importing it (`import lwfa_script`).
if __name__ == '__main__':

    # Initialize the simulation object
    sim = Simulation( Nz, zmax, Nr, rmax, Nm, dt, n_e=None, zmin=zmin,
            boundaries={"z":"open", "r":"reflective"}, n_order=n_order, use_cuda=use_cuda, verbose_level=2, )

    # Create a Gaussian laser profile
    laser_profile = GaussianLaser(a0=a0, waist=w0, tau=ctau / c, z0=z0,
                                  zf=None, theta_pol=0., lambda0=lambda0,
                                  cep_phase=0., phi2_chirp=0.,
                                  propagation_direction=1)

    # Add it to the simulation
    add_laser_pulse(sim, laser_profile, gamma_boost=None, method='direct', z0_antenna=None, v_antenna=0.)

    # Create the plasma electrons
    elec = sim.add_new_species( q=-e, m=m_e, n=n_e,
        dens_func=dens_func, p_zmin=p_zmin, p_zmax=p_zmax, p_rmax=p_rmax,
        p_nz=p_nz, p_nr=p_nr, p_nt=p_nt )

    if use_restart is False:
        # Track electrons if required (species 0 correspond to the electrons)
        if track_electrons:
            elec.track( sim.comm )
    else:
        # Load the fields and particles from the latest checkpoint file
        restart_from_checkpoint( sim )

    # Configure the moving window
    sim.set_moving_window( v=v_window )

    # Add diagnostics
    sim.diags = [ FieldDiagnostic( diag_period, sim.fld, comm=sim.comm ),
                  ParticleDiagnostic( diag_period, {"electrons" : elec},
                    select={"uz" : [1., None ]}, comm=sim.comm ) ]
    # Add checkpoints
    if save_checkpoints:
        set_periodic_checkpoint( sim, checkpoint_period )

    # Number of iterations to perform
    N_step = int(T_interact/sim.dt)

    # Get the fields in the half-plane theta=0 (Sum mode 0 and mode 1)
    gathered_grids = [sim.comm.gather_grid(sim.fld.interp[m]) for m in range(Nm)]

    rgrid = gathered_grids[0].r
    zgrid = gathered_grids[0].z

    # Check the Er field
    Er = gathered_grids[0].Er.T.real

    for m in range(1, Nm):
        # There is a factor 2 here so as to comply with the convention in
        # Lifschitz et al., which is also the convention adopted in Warp Circ
        Er += 2 * gathered_grids[m].Er.T.real

    # wavevector
    k0 = 2 * np.pi / lambda0
    # field amplitude
    e0 = m_e * c ** 2 * k0 / e

    fig = pyplot.figure(figsize=(8, 8))
    Plot2D(
        fig=fig,
        arr2d=Er / e0,
        h_axis=zgrid * 1e6,
        v_axis=rgrid * 1e6,
        zlabel=r"$E_r/E_0$",
        xlabel=r"$z \;(\mu m)$",
        ylabel=r"$r \;(\mu m)$",
        extent=(
            zgrid[0] * 1e6,  # + 40
            zgrid[-1] * 1e6,  # - 20
            rgrid[0] * 1e6,
            rgrid[-1] * 1e6,  # - 15,
        ),
        cbar=True,
        vmin=-2,
        vmax=2,
        hslice_val=0.0,  # do a 1D slice through the middle of the simulation box
    )
    fig.savefig('check_laser.png')

    # Run the simulation
    sim.step( N_step )
    print('')
