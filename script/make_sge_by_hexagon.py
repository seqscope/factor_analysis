import sys, io, gzip, os, copy, gc
import argparse
import numpy as np
import pandas as pd
from scipy.sparse import *

# Add parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hexagon_fn import *

parser = argparse.ArgumentParser()
parser.add_argument('--input', type=str, help='')
parser.add_argument('--gene_id_info', type=str, help='')
parser.add_argument('--output_pref', type=str, help='')
parser.add_argument('--mu_scale', type=float, default=26.67, help='Coordinate to um translate')
parser.add_argument('--key', default = 'gn', type=str, help='gt: genetotal, gn: gene, spl: velo-spliced, unspl: velo-unspliced, velo: velo total')
parser.add_argument('--hex_width', type=int, default=24, help='')
parser.add_argument('--precision', type=int, default=1, help='Number of digits to store spatial location (in um), 0 for integer.')
parser.add_argument('--hex_radius', type=int, default=-1, help='')
parser.add_argument('--min_ct_per_unit', type=int, default=20, help='')
parser.add_argument('--min_count_per_feature', type=int, default=50, help='')
parser.add_argument('--n_move', type=int, default=-1, help='')
args = parser.parse_args()

mu_scale = 1./args.mu_scale
radius=args.hex_radius
diam=args.hex_width
if radius < 0:
    radius = diam / np.sqrt(3)
else:
    diam = int(radius*np.sqrt(3))
if not os.path.exists(args.input):
    print(f"ERROR: cannot find input file \n {args.input}")
    sys.exit()

### Read data
try:
    df = pd.read_csv(args.input, sep='\t', usecols = ['X','Y','gene',args.key])
except:
    df = pd.read_csv(args.input, sep='\t', compression='bz2', usecols = ['X','Y','gene',args.key])

gene_name_to_id = {}
with gzip.open(args.gene_id_info, 'rt') as rf:
    for line in rf:
        wd = line.strip().split('\t')
        if len(wd) < 2:
            continue
        gene_name_to_id[wd[1]] = wd[0]

feature = df[['gene', args.key]].groupby(by = 'gene', as_index=False).agg({args.key:sum}).rename(columns = {args.key:'gene_tot'})
feature = feature.loc[feature.gene_tot > args.min_count_per_feature, :]
gene_kept = list(feature['gene'])
gene_kept = [x for x in gene_kept if x in gene_name_to_id]
if len(gene_kept) < feature.shape[0]:
    print(f"Warning: not all genes found corresponding ENSEMBL ID")
    feature = feature.loc[feature.gene.isin(gene_kept)]
feature['gene_id'] = feature.gene.map(gene_name_to_id)
df = df[df.gene.isin(gene_kept)]
df['j'] = df.X.astype(str) + '_' + df.Y.astype(str)

brc = df.groupby(by = ['j','X','Y']).agg({args.key: sum}).reset_index()
brc.index = range(brc.shape[0])
pixel_ct = brc[args.key].values
pts = np.asarray(brc[['X','Y']]) * mu_scale
print(f"Read data with {brc.shape[0]} pixels and {len(gene_kept)} genes.")
df.drop(columns = ['X', 'Y'], inplace=True)

# Make DGE
feature_kept = copy.copy(gene_kept)
barcode_kept = list(brc.j.values)
del brc
gc.collect()
bc_dict = {x:i for i,x in enumerate( barcode_kept ) }
ft_dict = {x:i for i,x in enumerate( feature_kept ) }
indx_row = [ bc_dict[x] for x in df['j']]
indx_col = [ ft_dict[x] for x in df['gene']]
N = len(barcode_kept)
M = len(feature_kept)
T = df[args.key].sum()
dge_mtx = coo_matrix((df[args.key].values, (indx_row, indx_col)), shape=(N, M)).tocsr()
feature_mf = np.asarray(dge_mtx.sum(axis = 0)).reshape(-1)
feature_mf = feature_mf / feature_mf.sum()
total_molecule=df[args.key].sum()
print(f"Made DGE {dge_mtx.shape}")
del df
gc.collect()

feature['dummy'] = "Gene Expression"
f = args.output_pref + "features.tsv.gz"
feature[['gene_id','gene','dummy']].to_csv(f, sep='\t', index=False, header=False)

brc_f = args.output_pref + "barcode.tsv"
mtx_f = args.output_pref + "matrix.mtx"
# If exists, delete 
if os.path.exists(brc_f):
    _ = os.system("rm " + brc_f)
if os.path.exists(mtx_f):
    _ = os.system("rm " + mtx_f)

n_move = args.n_move
if n_move > diam or n_move < 0:
    n_move = diam // 4

b_size = 512
offs_x = 0
offs_y = 0
n_unit = 0
while offs_x < n_move:
    while offs_y < n_move:
        x,y = pixel_to_hex(pts, radius, offs_x/n_move, offs_y/n_move)
        hex_crd = list(zip(x,y))
        ct = pd.DataFrame({'hex_id':hex_crd, 'tot':pixel_ct}).groupby(by = 'hex_id').agg({'tot': sum}).reset_index()
        mid_ct = np.median(ct.loc[ct.tot >= args.min_ct_per_unit, 'tot'].values)
        ct = set(ct.loc[ct.tot >= args.min_ct_per_unit, 'hex_id'].values)
        hex_list = list(ct)
        hex_dict = {x:i for i,x in enumerate(hex_list)}
        sub = pd.DataFrame({'crd':hex_crd,'cCol':range(N), 'X':pts[:, 0], 'Y':pts[:, 1]})
        sub = sub[sub.crd.isin(ct)]
        sub['cRow'] = sub.crd.map(hex_dict).astype(int)

        brc = sub[['cRow','X', 'Y']].groupby(by = 'cRow').agg({'X':np.mean, 'Y':np.mean}).reset_index()
        brc['X'] = [f"{x:.{args.precision}f}" for x in brc.X.values]
        brc['Y'] = [f"{x:.{args.precision}f}" for x in brc.Y.values]
        brc.sort_values(by = 'cRow', inplace=True)
        with open(brc_f, 'a') as wf:
            _ = wf.write('\n'.join((brc.cRow+n_unit+1).astype(str).values + '_' + brc.X.values + '_' + brc.Y.values)+'\n')

        n_hex = len(hex_dict)
        n_minib = n_hex // b_size
        print(f"{n_minib}, {n_hex} ({sub.cRow.max()}, {sub.shape[0]}), median count per unit {mid_ct}")
        if n_hex < b_size // 4:
            offs_y += 1
            continue
        grd_minib = list(range(0, n_hex, b_size))
        grd_minib[-1] = n_hex 
        st_minib = 0
        n_minib = len(grd_minib) - 1
        
        while st_minib < n_minib:
            indx_minib = (sub.cRow >= grd_minib[st_minib]) & (sub.cRow < grd_minib[st_minib+1])
            npixel_minib = sum(indx_minib)
            offset = sub.loc[indx_minib, 'cRow'].min()
            nhex_minib = sub.loc[indx_minib, 'cRow'].max() - offset + 1

            mtx = coo_matrix((np.ones(npixel_minib, dtype=bool), (sub.loc[indx_minib, 'cRow'].values-offset, sub.loc[indx_minib, 'cCol'].values)), shape=(nhex_minib, N) ).tocsr() @ dge_mtx

            mtx.eliminate_zeros()
            r, c = mtx.nonzero()
            r = np.array(r,dtype=int) + n_unit + 1
            c = np.array(c,dtype=int) + 1
            n_unit += mtx.shape[0]
            mtx = pd.DataFrame({'i':c, 'j':r, 'v':mtx.data})
            mtx['i'] = mtx.i.astype(int)
            mtx['j'] = mtx.j.astype(int) 
            mtx.to_csv(mtx_f, mode='a', sep=' ', index=False, header=False)
            st_minib += 1
            print(f"{st_minib}/{n_minib}. Wrote {n_unit} units so far.")

        print(f"Sliding offset {offs_x}, {offs_y}. Fit data with {n_unit} units.")
        offs_y += 1
    offs_y = 0
    offs_x += 1

_ = os.system("gzip -f " + brc_f)

mtx_header = args.output_pref + ".matrix.header"
with open(mtx_header, 'w') as wf:
    line = "%%MatrixMarket matrix coordinate integer general\n%\n"
    line += " ".join([str(x) for x in [M, n_unit, T]]) + "\n"
    wf.write(line)

arg = " ".join(["cat",mtx_header,mtx_f,"|gzip -c > ", mtx_f+".gz"])
if os.system(arg) == 0:
    _ = os.system("rm " + mtx_f)