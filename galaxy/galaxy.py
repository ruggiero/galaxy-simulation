# Run python galaxy.py --help for a description.

from os import path, remove
from sys import exit, stdout, path as syspath
from time import sleep

import numpy as np
import numpy.random as nprand
from numpy import cos, sin, pi, arccos, log10, exp, arctan, cosh
from scipy.optimize import brentq
from bisect import bisect_left
from multiprocessing import Process, Array
from argparse import ArgumentParser as parser
from itertools import product

from treecode import oct_tree, potential
from snapwrite import process_input, write_snapshot
syspath.append(path.join(path.dirname(__file__), '..', 'misc'))
from units import temp_to_internal_energy


G = 43007.1


def main():
  init()
  galaxy_data = generate_galaxy()
  write_input_file(galaxy_data)


def init():
  global M_halo, M_disk, M_bulge, M_gas
  global N_halo, N_disk, N_bulge, N_gas
  global a_halo, a_bulge, Rd, z0, z0_gas
  global N_total, M_total
  global phi_grid, rho_axis, z_axis, N_rho, Nz
  global halo_core, bulge_core, N_CORES, force_yes, output, gas, factor
  flags = parser(description="Generates an initial conditions file for a\
                              galaxy simulation with halo, stellar disk,\
                              gaseous disk and bulge components.")
  flags.add_argument('--nogas', help='Generates a galaxy without gas.',
                     action='store_true')
  flags.add_argument('-cores', help='The number of cores to use during the\
                                     potential canculation. Make sure this\
                                     number is a factor of N_rho*N_z.',
                     default=1)
  flags.add_argument('--force-yes', help='Don\'t ask if you want to use the\
                                          existing potential_data.txt file.\
                                          Useful for automating the execution\
                                          of the script.', action='store_true')
  flags.add_argument('-o', help='The name of the output file.',
                     metavar="init.dat", default="init.dat")
  args = flags.parse_args()
  gas = not args.nogas
  N_CORES = int(args.cores)
  force_yes = args.force_yes
  output = args.o

  if not (path.isfile("header.txt") and path.isfile("galaxy_param.txt")):
    print "header.txt or galaxy_param.txt missing."
    exit(0)

  vars_ = process_input("galaxy_param.txt")
  M_halo, M_disk, M_bulge, M_gas = (float(i[0]) for i in vars_[0:4])
  N_halo, N_disk, N_bulge, N_gas = (float(i[0]) for i in vars_[4:8])
  a_halo, a_bulge, Rd, z0, z0_gas = (float(i[0]) for i in vars_[8:13])
  halo_core, bulge_core = (i[0][:-1] == 'True' for i in vars_[13:15])
  factor = float(vars_[15][0])
  z0_gas *= z0
  if not gas:
    N_gas = 0
    M_gas = 0
  M_total = M_disk + M_bulge + M_halo + M_gas
  N_total = N_disk + N_bulge + N_halo + N_gas
  N_rho = Nz = 2000 # Make sure N_CORES is a factor of N_rho*Nz.
  phi_grid = np.zeros((N_rho, Nz))
  rho_max = 300 * a_halo
  # This has to go far so I can estimate the integrals below.
  z_max = 3000 * a_halo 
  rho_axis = np.logspace(-2, log10(rho_max), N_rho)
  z_axis = np.logspace(-2, log10(z_max), Nz)


def generate_galaxy():
  global phi_grid
  print "Setting positions..."
  coords_halo = set_halo_positions()
  coords_stars = set_disk_positions(N_disk, z0)
  coords_bulge = set_bulge_positions()
  if(gas):
    coords_gas = set_disk_positions(N_gas, z0_gas)
    coords = np.concatenate((coords_gas, coords_halo, coords_stars,
                             coords_bulge))
    coords_disk = np.concatenate((coords_gas, coords_stars))
  else:
    coords = np.concatenate((coords_halo, coords_stars, coords_bulge))
    coords_disk = coords_stars

  if path.isfile('potential_data.txt'):
    if not force_yes:
      print ("Use existing potential tabulation in potential_data.txt?\
              Make sure it refers to the current parameters. (y/n)")
      ans = raw_input()
      while ans not in "yn":
        print "Please give a proper answer. (y/n)"
        ans = raw_input()
    else: ans = "y"
    if ans == "y":
      phi_grid = np.loadtxt('potential_data.txt')
    else:
      remove('potential_data.txt')
      fill_potential_grid(coords_disk)
      np.savetxt('potential_data.txt', phi_grid)
  else:
    fill_potential_grid(coords_disk)
    np.savetxt('potential_data.txt', phi_grid)
  if(gas):
    print "Setting temperatures..."
    U, T_cl_grid = set_temperatures(coords_gas) 
    print "Setting densitites..."
    rho = set_densities(coords_gas)
    print "Setting velocities..."
    vels = set_velocities(coords, T_cl_grid) 
  else:
    print "Setting velocities..."
    vels = set_velocities(coords, None) 
  coords = np.array(coords, order='C')
  coords.shape = (1, -1) # Linearizing the array.
  vels = np.array(vels, order='C')
  vels.shape = (1, -1)
  if(gas):
    return [coords[0], vels[0], U, rho]
  else:
    return [coords[0], vels[0]]


def dehnen_inverse_cumulative(Mc, M, a, core):
  if(core):
    return ((a * (Mc**(2/3.)*M**(4/3.) + Mc*M + Mc**(4/3.)*M**(2/3.))) /
            (Mc**(1/3.) * M**(2/3.) * (M-Mc)))
  else:
    return (a * ((Mc*M)**0.5 + Mc)) / (M-Mc)


def dehnen_potential(r, M, a, core):
  if(core):
    return (G*M)/(2*a) * ((r/(r+a))**2 - 1)
  else:
    return (G*M)/a * (r/(r+a) - 1)


def halo_density(r):
  if(halo_core):
    return (3*M_halo)/(4*pi) * a_halo/(r+a_halo)**4
  else:
    return M_halo/(2*pi) * a_halo/(r*(r+a_halo)**3)


def disk_density(rho, z, M, z0):
  cte = M/(4*pi*z0*Rd**2)
  return cte * (1/cosh(z/z0))**2 * exp(-rho/Rd)
 

def bulge_density(r):
  if(bulge_core):
    return (3*M_bulge)/(4*pi) * a_bulge/(r+a_bulge)**4
  else:
    return M_bulge/(2*pi) * a_bulge/(r*(r+a_bulge)**3)


def set_halo_positions():
  # The factor M * 200^2 / 201^2 restricts the radius to 200 * a.
  radii = dehnen_inverse_cumulative(nprand.sample(N_halo) *
    ((M_halo*40000) / 40401), M_halo, a_halo, halo_core)
  thetas = np.arccos(nprand.sample(N_halo)*2 - 1)
  phis = 2 * pi * nprand.sample(N_halo)
  xs = radii * sin(thetas) * cos(phis)
  ys = radii * sin(thetas) * sin(phis)
  zs = radii * cos(thetas)
  coords = np.column_stack((xs, ys, zs))
  return coords


def set_bulge_positions():
  radii = dehnen_inverse_cumulative(nprand.sample(N_bulge) *
    ((M_bulge*40000) / 40401), M_bulge, a_bulge, bulge_core)
  thetas = np.arccos(nprand.sample(N_bulge)*2 - 1)
  phis = 2 * pi * nprand.sample(N_bulge)
  xs = radii * sin(thetas) * cos(phis)
  ys = radii * sin(thetas) * sin(phis)
  zs = radii * cos(thetas)
  coords = np.column_stack((xs, ys, zs))
  return coords


def set_disk_positions(N, z0):
  radii = np.zeros(N)
  # The maximum radius is restricted to 60 kpc.
  sample = nprand.sample(N) * disk_radial_cumulative(60)
  for i, s in enumerate(sample):
    radii[i] = disk_radial_inverse_cumulative(s)
  zs = disk_height_inverse_cumulative(nprand.sample(N), z0)
  phis = 2 * pi * nprand.sample(N)
  xs = radii * cos(phis)
  ys = radii * sin(phis)
  coords = np.column_stack((xs, ys, zs))
  return coords


def disk_radial_cumulative(r):
  return (Rd**2-(Rd**2+r*Rd)*exp(-r/Rd))/Rd**2


# 'frac' is a number between 0 and 1.
def disk_radial_inverse_cumulative(frac):
  return brentq(lambda r: disk_radial_cumulative(r) - frac, 0, 1.0e10)


def disk_height_inverse_cumulative(frac, z0):
  return 0.5 * z0 * np.log(frac/(1-frac))


def interpolate(value, axis):
  index = bisect_left(axis, value)
  if(index >= len(axis)-1):
    return len(axis)-1
  else:
    return index


def fill_potential_grid(coords_disk):
  ps = []
  # Indexes are randomly distributed across processors for higher
  # performance. The tree takes longer to calculate the potential
  # at small radii. ip stands for 'index pair'.
  ip = nprand.permutation(list(product(range(N_rho), range(Nz))))
  print "Building gravity tree..."
  gravtree = oct_tree(200*a_halo*2)
  for i, part in enumerate(coords_disk):
    prog = 100*float(i)/len(coords_disk)
    stdout.write("%.2f%% done\r" % prog)
    stdout.flush()
    gravtree.insert(part, M_disk/N_disk)
  print ("Filling potential grid...")
  def loop(n_loop, N_CORES):
    for i in range(n_loop*N_rho*Nz/N_CORES, (1+n_loop)*N_rho*Nz/N_CORES):
      prog[n_loop] = 100*float(i-n_loop*N_rho*Nz/N_CORES)/(N_rho*Nz/N_CORES)
      m = ip[i][0]
      n = ip[i][1]
      r = (rho_axis[m]**2 + z_axis[n]**2)**0.5
      shared_phi_grid[m][n] += dehnen_potential(r, M_halo, a_halo,
        halo_core)
      shared_phi_grid[m][n] += potential(np.array((rho_axis[m], 0, z_axis[n])),
        gravtree)
      shared_phi_grid[m][n] += dehnen_potential(r, M_bulge, a_bulge, bulge_core)
  shared_phi_grid = [Array('f', phi_grid[i]) for i in range(len(phi_grid))]
  prog = Array('f', [0, 0])
  proc=[Process(target=loop, args=(n, N_CORES)) for n in range(N_CORES)]
  try:
    [p.start() for p in proc]
    while np.all([p.is_alive() for p in proc]):
      for i in range(N_CORES):
        if i == N_CORES - 1:
          stdout.write("%1.1f%% done at core %d\r" % (prog[N_CORES-1], N_CORES))
        else:
          stdout.write("%1.1f%% done at core %d, " % (prog[i], i+1))
      stdout.flush()
      sleep(0.1)
    [p.join() for p in proc]
  except KeyboardInterrupt:
    [p.terminate() for p in proc]
    [p.join() for p in proc]
    print "\nProcess canceled."
    exit(0)
  for i in range(N_rho):
    for j in range(Nz):
      phi_grid[i][j] = shared_phi_grid[i][j]


# Calculates the second radial partial derivative of the potential
# at the point (rho_axis[i], z_axis[j]). As the grid is unevenly spaced,
# a more complicated formula must be used. Formula taken from
# http://mathformeremortals.wordpress.com/
# 2013/01/12/a-numerical-second-derivative-from-three-points/
def d2phi_drho2(i, j):
  x1, x2, x3 = rho_axis[i-1], rho_axis[i], rho_axis[i+1]
  y1, y2, y3 = phi_grid[i-1][j], phi_grid[i][j], phi_grid[i+1][j]
  v1 = np.array((2/((x2-x1)*(x3-x1)),-2/((x3-x2)*(x2-x1)),2/((x3-x2)*(x3-x1))))
  v2 = np.array((y1, y2, y3))
  return np.dot(v1, v2)


def generate_sigma_grids():
  # The [0], [1] and [2] components of this grid will refer to the halo,
  # disk and bulge, respectively. The calculation being performed here
  # follows the prescription found in Springel & White, 1999.
  sz_grid = np.zeros((3, N_rho, Nz))
  ys = np.zeros((3, N_rho, Nz)) # Integrand array.
  # ys is the integrand array. Filling it.
  for i in range(N_rho):
    for j in range(0, Nz-1):
      r = (rho_axis[i]**2 + z_axis[j]**2)**0.5
      dz = z_axis[j+1] - z_axis[j]
      dphi = phi_grid[i][j+1] - phi_grid[i][j]
      ys[0][i][j] = halo_density(r) * dphi/dz 
      ys[1][i][j] = disk_density(rho_axis[i], z_axis[j], M_disk, z0) * dphi/dz
      ys[2][i][j] = bulge_density(r) * dphi/dz 
    for j in range(0, Nz-1):
      r = (rho_axis[i]**2 + z_axis[j]**2)**0.5
      sz_grid[0][i][j] = 1/halo_density(r) * np.trapz(ys[0][i][j:], z_axis[j:])
      sz_grid[1][i][j] = (1/disk_density(rho_axis[i], z_axis[j], M_disk, z0) * 
        np.trapz(ys[1][i][j:], z_axis[j:]))
      sz_grid[2][i][j] = 1/bulge_density(r) * np.trapz(ys[2][i][j:], z_axis[j:])

  sphi_grid = np.zeros((3, N_rho, Nz))
#  aux_grid = np.zeros(N_rho)
  for i in range(1, N_rho-1):
    for j in range(Nz):
      r0 = (rho_axis[i]**2 + z_axis[j]**2)**0.5
      r1 = (rho_axis[i+1]**2 + z_axis[j]**2)**0.5
      drho = rho_axis[i+1] - rho_axis[i]
      dphi = phi_grid[i+1][j] - phi_grid[i][j]
      sphi_grid[0][i][j] = (sz_grid[0][i][j] + rho_axis[i]/halo_density(r0) * 
        (halo_density(r1)*sz_grid[0][i+1][j] - 
        halo_density(r0)*sz_grid[0][i][j]) / drho + rho_axis[i] * dphi/drho)
      if(j == 0):
        kappa2 = 3/rho_axis[i] * dphi/drho + d2phi_drho2(i, j)
        gamma2 = 4/(kappa2*rho_axis[i]) * dphi/drho
        sphi_grid[1][i][j] = sz_grid[1][i][j] / gamma2
#        aux_grid[i] = (sz_grid[1][i][j] + 
#          rho_axis[i]/disk_density(rho_axis[i], z_axis[j], M_disk, z0) * 
#          (sz_grid[1][i+1][j]*disk_density(rho_axis[i+1], z_axis[j], M_disk, z0) -
#          sz_grid[1][i][j]*disk_density(rho_axis[i], z_axis[j], M_disk, z0)) /
#          drho + rho_axis[i] * dphi/drho)
        if i == N_rho-2:
          sphi_grid[1][0][j] = sphi_grid[1][1][j]
#          aux_grid[0] = aux_grid[1]
#          aux_grid[N_rho-1] = aux_grid[N_rho-2]
      sphi_grid[2][i][j] = (sz_grid[2][i][j] + rho_axis[i]/bulge_density(r0) * 
        (bulge_density(r1)*sz_grid[2][i+1][j] - 
        bulge_density(r0)*sz_grid[2][i][j]) / drho + rho_axis[i] * dphi/drho)
      for k in [0, 2]:
        sphi_grid[k][0][j] = sphi_grid[k][1][j]
#  return sz_grid, sphi_grid, aux_grid
  return sz_grid, sphi_grid


def set_velocities(coords, T_cl_grid):
  sz_grid, sphi_grid = generate_sigma_grids()
  # Avoiding numerical problems. They only occur at a minor amount
  # of points, anyway. I set the values to a small number so I can
  # successfuly sample from the gaussian distributions ahead.
  sphi_grid[np.isnan(sphi_grid)] = 1.0e-5;
  sphi_grid[sphi_grid == np.inf] = 1.0e-5;
  sphi_grid[sphi_grid <= 0] = 1.0e-5;

#  aux_grid[np.isnan(aux_grid)] = 1.0e-5;
#  aux_grid[aux_grid == np.inf] = 1.0e-5;
#  aux_grid[aux_grid <= sphi_grid[1][0]] = (sphi_grid[1][aux_grid <= sphi_grid[1][0]] + 1.0e-5)

  vels = np.zeros((N_total, 3))
  vphis = {}
  for i, part in enumerate(coords):
    x = part[0]
    y = part[1]
    z = abs(part[2])
    rho = (x**2 + y**2)**0.5
    r = (rho**2 + z**2)**0.5
    if(x > 0 and y > 0):
      phi = arctan(y/x)
    elif(x < 0 and y > 0):
      phi = pi - arctan(-y/x)
    elif(x < 0 and y < 0):
      phi = pi + arctan(y/x)
    elif(x > 0 and y < 0):
      phi = 2 * pi - arctan(-y/x)
    bestz = interpolate(z, z_axis)
    bestr = interpolate(rho, rho_axis)
    if(i < N_gas):
      dphi = phi_grid[bestr][bestz]-phi_grid[bestr-1][bestz]
      drho = rho_axis[bestr]-rho_axis[bestr-1]
      dP = (disk_density(rho_axis[bestr], z, M_gas, z0_gas)*
          T_cl_grid[bestr][bestz] - disk_density(rho_axis[bestr-1], 
          z, M_gas, z0_gas)*T_cl_grid[bestr-1][bestz])
      vphi2 = rho * (dphi/drho + 1/disk_density(rho, z, M_gas,
          z0_gas) * dP/drho)
      vphi = abs(vphi2)**0.5
      vz = vr = 0
    elif(i >= N_gas and i < N_gas+N_halo):
      sigmaz = sz_grid[0][bestr][bestz]
      sigmap = sphi_grid[0][bestr][bestz]
      vz = nprand.normal(scale=sigmaz**0.5)
      vr = nprand.normal(scale=sigmaz**0.5)
      vphi = nprand.normal(scale=sigmap**0.5)
    elif(i >= N_gas+N_halo and i < N_gas+N_halo+N_disk):
      sigmaz = sz_grid[1][bestr][bestz]
      sigmap = sphi_grid[1][bestr][0]
      vz = nprand.normal(scale=sigmaz**0.5)
      vr = nprand.normal(scale=factor*sigmaz**0.5)
      vphi = nprand.normal(scale=factor*sigmap**0.5)
      if(bestz not in vphis):
        tck = inter.splrep(rho_axis, phi_grid[:, bestz])
        vphis[bestz] = inter.interp1d(rho_axis, inter.splev(rho_axis, tck, der=1))
      if(vphis[bestz](rho) > 0):
        vphi += (rho * vphis[bestz](rho))**0.5
    else:
      sigmaz = sz_grid[2][bestr][bestz]
      sigmap = sphi_grid[2][bestr][bestz]
      vz = nprand.normal(scale=sigmaz**0.5)
      vr = nprand.normal(scale=sigmaz**0.5)
      vphi = nprand.normal(scale=sigmap**0.5)
    vels[i][0] = vr*cos(phi) - vphi*sin(phi)
    vels[i][1] = vr*sin(phi) + vphi*cos(phi)
    vels[i][2] = vz
  return vels
 

def set_densities(coords_gas):
  rhos = np.zeros(N_gas)
  for i, part in enumerate(coords_gas):
    rho = (part[0]**2 + part[1]**2)**0.5
    z = abs(part[2])
    rhos[i] = disk_density(rho, z, M_gas, z0_gas)
  return rhos


def set_temperatures(coords_gas):
  U_grid = np.zeros((N_rho, Nz))
  U = np.zeros(N_gas)
  # Constantless temperature, will be used in the circular
  # velocity determination for the gas.
  T_cl_grid = np.zeros((N_rho, Nz)) 
  MP_OVER_KB = 121.148
  HYDROGEN_MASSFRAC = 0.76
  meanweight_n = 4.0 / (1 + 3 * HYDROGEN_MASSFRAC)
  meanweight_i = 4.0 / (3 + 5 * HYDROGEN_MASSFRAC)
  ys = np.zeros((N_rho, Nz)) # Integrand array.
  for i in range(N_rho):
    for j in range(1, Nz):
      dphi = phi_grid[i][j] - phi_grid[i][j-1]
      dz = z_axis[j] - z_axis[j-1]
      ys[i][j] = (disk_density(rho_axis[i], z_axis[j], M_gas, z0_gas) *
            dphi/dz)
    ys[i][0] = ys[i][1]
    for j in range(0, Nz-1):
      result = (np.trapz(ys[i][j:], z_axis[j:]) /
            disk_density(rho_axis[i], z_axis[j], M_gas, z0_gas))
      temp_i = MP_OVER_KB * meanweight_i * result
      temp_n = MP_OVER_KB * meanweight_n * result
      if(temp_i > 1.0e4):
        U_grid[i][j] = temp_to_internal_energy(temp_i)
      else:
        U_grid[i][j] = temp_to_internal_energy(temp_n)
      T_cl_grid[i][j] = result
    U_grid[i][-1] = U_grid[i][-2]
    T_cl_grid[i][-1] = T_cl_grid[i][-2]
  for i, part in enumerate(coords_gas):
    rho = (part[0]**2 + part[1]**2)**0.5
    z = abs(part[2])
    bestz = interpolate(z, z_axis)
    bestr = interpolate(rho, rho_axis)
    U[i] = U_grid[bestr][bestz]
  #U.fill(temp_to_internal_energy(disk_temp))
  #if(disk_temp > 1.0e4):
  #  T_cl_grid.fill(disk_temp / MP_OVER_KB / meanweight_i)
  #else:
  #  T_cl_grid.fill(disk_temp / MP_OVER_KB / meanweight_n)
  return U, T_cl_grid


def write_input_file(galaxy_data):
  coords = galaxy_data[0]
  vels = galaxy_data[1]
  ids = np.arange(1, N_total+1, 1)
  m_halo = np.empty(N_halo)
  m_halo.fill(M_halo/N_halo)
  m_disk = np.empty(N_disk)
  m_disk.fill(M_disk/N_disk)
  m_bulge = np.empty(N_bulge)
  m_bulge.fill(M_bulge/N_bulge)
  if(gas):
    U = galaxy_data[2]
    rho = galaxy_data[3]
    m_gas = np.empty(N_gas)
    m_gas.fill(M_gas/N_gas)
    masses = np.concatenate((m_gas, m_halo, m_disk, m_bulge))
    smooths = np.zeros(N_gas)
    write_snapshot(n_part=[N_gas, N_halo, N_disk, N_bulge, 0, 0],
      from_text=False, outfile=output,
      data_list=[coords, vels, ids, masses, U, rho, smooths])
  else:
    masses = np.concatenate((m_halo, m_disk, m_bulge))
    write_snapshot(n_part=[0, N_halo, N_disk, N_bulge, 0, 0],
      from_text=False, outfile=output,
      data_list=[coords, vels, ids, masses])


if __name__ == '__main__':
  main()
