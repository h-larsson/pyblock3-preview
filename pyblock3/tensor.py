
from enum import Enum, auto
from collections import Counter
import numpy as np


class FusingTypes(Enum):
    """tensor types for determining fermion phase factor and fusing"""
    # scalar: this is generated by MPS @ MPS
    Scalar = auto()
    # legs: left - mid - right
    ThreeIndexMPSTensor = auto()
    # legs: left1 - left2 - mid - right1 - right2
    # this is generated by MPO @ MPS
    UnfusedThreeIndexMPSTensor = auto()
    # legs: (left, mid) - right
    LeftFusedMPSTensor = auto()
    # legs: left, (mid, right)
    RightFusedMPSTensor = auto()
    # legs: left - mid1 - mid2 - right
    TwoSiteMPSTensor = auto()
    # legs: left - up - down - right
    FourIndexMPOTensor = auto()
    # legs: left1 - left2 - up - down - right1 - right2
    # this is generated by MPO @ MPO
    UnfusedFourIndexMPOTensor = auto()
    # legs: left - up1 - down1 - up2 - down2 - right
    TwoSiteMPOTensor = auto()


class SubTensor:
    """
    A block in block-sparse tensor.

    Attributes:
        q_labels : tuple(SZ..)
            Quantum labels for this sub-tensor block.
            Each element in the tuple corresponds one rank of the tensor.
        reduced : numpy.ndarray
            Rank-:attr:`rank` dense reduced matrix.
        rank : int
            Rank of the tensor. ``rank == len(q_labels)``.
    """

    def __init__(self, q_labels=None, reduced=None):
        self.q_labels = tuple(q_labels) if q_labels is not None else ()
        self.rank = len(q_labels)
        self.reduced = reduced
        if self.rank != 0:
            if reduced is not None:
                assert len(self.reduced.shape) == self.rank

    def randomize(self, a=0, b=1):
        """Set reduced matrix with random numbers in [0, 1)."""
        self.reduced = np.random.random(self.reduced.shape) * (b - a) + a

    def clear(self):
        """Set reduced matrix to zero."""
        self.reduced = np.zeros(self.reduced.shape)

    def copy(self):
        """Shallow copy."""
        return SubTensor(q_labels=self.q_labels, reduced=self.reduced)

    def __mul__(self, other):
        """Scalar multiplication."""
        return SubTensor(q_labels=self.q_labels, reduced=other * self.reduced)

    def __neg__(self):
        """Times (-1)."""
        return SubTensor(q_labels=self.q_labels, reduced=-self.reduced)

    def equal_shape(self, other):
        """Test if two blocks have equal shape and quantum labels."""
        return self.q_labels == other.q_labels and self.reduced.shape == other.reduced.shape

    def __eq__(self, other):
        return self.q_labels == other.q_labels and np.allclose(self.reduced, other.reduced)

    def __repr__(self):
        return "(Q=) %r (R=) %r" % (self.q_labels, self.reduced)


class SparseTensor:
    """
    block-sparse tensor
    can support either fused/unfused
    """

    def __init__(self, blocks=None, ftype=None, delta_quantum=None):
        self.blocks = blocks if blocks is not None else []
        self.delta_quantum = delta_quantum
        if ftype is None:
            if self.rank == 3:
                self.ftype = FusingTypes.ThreeIndexMPSTensor
            elif self.rank == 4:
                self.ftype = FusingTypes.FourIndexMPOTensor
            else:
                raise RuntimeError("Cannot determine sparse-tensor type!")
        else:
            self.ftype = ftype

    @property
    def rank(self):
        """Rank of SparseTensor"""
        return 0 if len(self.blocks) == 0 else self.blocks[0].rank

    @property
    def n_blocks(self):
        """Number of (non-zero) blocks."""
        return len(self.blocks)

    @staticmethod
    def init_from_state_info(l, m, r):
        blocks = []
        for kl, vl in l.quanta.items():
            for km, vm in m.quanta.items():
                kr = kl + km
                if kr in r.quanta.items():
                    blocks.append(SubTensor(q_labels=(kl, km, kr),
                                            reduced=np.zeros((vl, vm, r[kr]))))
        return SparseTensor(blocks=blocks, ftype=FusingTypes.ThreeIndexMPSTensor, delta_quantum=SZ())

    def randomize(self, a=0, b=1):
        for block in self.blocks:
            block.randomize(a, b)

    def get_state_info(self, idx):
        """get state info associated with one of the legs
        idx: leg index"""
        quanta = Counter()
        for block in self.blocks:
            q = block.q_labels[idx]
            if q in quanta:
                assert block.reduced.shape[idx] == quanta[q]
            else:
                quanta[q] = block.reduced.shape[idx]
        return StateInfo(quanta)

    @staticmethod
    def contract(spta, sptb, idxa, idxb):
        """
        Contract two SparseTensor to form a new SparseTensor.

        Args:
            spta : SparseTensor
                SparseTensor a, as left operand.
            sptb : SparseTensor
                SparseTensor b, as right operand.
            idxa : list(int)
                Indices of rank to be contracted in SparseTensor a.
            idxb : list(int)
                Indices of rank to be contracted in SparseTensor b.
        Returns:
            SparseTensor : SparseTensor
                The contracted SparseTensor.
        """
        assert len(idxa) == len(idxb)
        idxa = [x if x >= 0 else spta.rank + x for x in idxa]
        idxb = [x if x >= 0 else sptb.rank + x for x in idxb]
        out_idx_a = list(set(range(0, spta.rank)) - set(idxa))
        out_idx_b = list(set(range(0, sptb.rank)) - set(idxb))

        map_idx_b = {}
        for block in sptb.blocks:
            subg = tuple(block.q_labels[id] for id in idxb)
            if subg not in map_idx_b:
                map_idx_b[subg] = []
            map_idx_b[subg].append(block)

        # fermionic phase factor
        def fermion(qa, qb): return False
        out_ftype = None
        # op x mps (1-site)
        if spta.ftype == FusingTypes.FourIndexMPOTensor and sptb.ftype == FusingTypes.ThreeIndexMPSTensor:
            if idxa == [2] and idxb == [1]:
                def fermion(qa, qb): return (
                    qa[1] + qa[2]).is_fermion and qb[0].is_fermion
                out_ftype = FusingTypes.ThreeIndexMPSTensor

        map_idx_out = {}
        for block_a in spta.blocks:
            subg = tuple(block_a.q_labels[id] for id in idxa)
            if subg in map_idx_b:
                outga = tuple(block_a.q_labels[id] for id in out_idx_a)
                for block_b in map_idx_b[subg]:
                    outg = outga + \
                        tuple(block_b.q_labels[id] for id in out_idx_b)
                    outd = tuple(x for x in outg)
                    mat = np.tensordot(
                        block_a.reduced, block_b.reduced, axes=(idxa, idxb))
                    if fermion(block_a.q_labels, block_b.q_labels)
                    mat *= -1
                    if outd not in map_idx_out:
                        map_idx_out[outd] = SubTensor(
                            q_labels=outg, reduced=mat)
                    else:
                        map_idx_out[outd].reduced += mat
        if len(out_idx_a) + len(out_idx_b) == 0:
            if len(map_idx_out) == 0:
                return 0.0
            return map_idx_out[()].reduced.item()
        else:
            return SparseTensor(blocks=list(map_idx_out.values()), ftype=out_ftype)

    def left_canonicalize(self, mode='reduced'):
        """
        Left canonicalization (using QR factorization).
        Left canonicalization needs to collect all left indices for each specific right index.
        So that we will only have one R, but left dim of q is unchanged.

        Returns:
            r_blocks : dict(q_label_r -> numpy.ndarray)
                The R matrix for each right-index quantum label.
        """
        collected_rows = {}
        for block in self.blocks:
            q_label_r = block.q_labels[-1]
            if q_label_r not in collected_rows:
                collected_rows[q_label_r] = []
            collected_rows[q_label_r].append(block)
        r_blocks_map = {}
        for q_label_r, blocks in collected_rows.items():
            l_shapes = [np.prod(b.reduced.shape[:-1]) for b in blocks]
            mat = np.concatenate([b.reduced.reshape((sh, -1))
                                  for sh, b in zip(l_shapes, blocks)], axis=0)
            q, r = np.linalg.qr(mat, mode)
            r_blocks_map[q_label_r] = r
            qs = np.split(q, list(accumulate(l_shapes[:-1])), axis=0)
            assert(len(qs) == len(blocks))
            for q, b in zip(qs, blocks):
                b.reduced = q.reshape(b.reduced.shape[:-1] + (r.shape[0], ))
        return r_blocks_map

    def right_canonicalize(self, mode='reduced'):
        """
        Right canonicalization (using QR factorization).

        Returns:
            l_blocks : dict(q_label_l -> numpy.ndarray)
                The L matrix for each left-index quantum label.
        """
        collected_cols = {}
        for block in self.blocks:
            q_label_l = block.q_labels[0]
            if q_label_l not in collected_cols:
                collected_cols[q_label_l] = []
            collected_cols[q_label_l].append(block)
        l_blocks_map = {}
        for q_label_l, blocks in collected_cols.items():
            r_shapes = [np.prod(b.reduced.shape[1:]) for b in blocks]
            mat = np.concatenate([b.reduced.reshape((-1, sh)).T
                                  for sh, b in zip(r_shapes, blocks)], axis=0)
            q, r = np.linalg.qr(mat, mode)
            l_blocks_map[q_label_l] = r.T
            qs = np.split(q, list(accumulate(r_shapes[:-1])), axis=0)
            assert(len(qs) == len(blocks))
            for q, b in zip(qs, blocks):
                b.reduced = q.T.reshape((r.shape[0], ) + b.reduced.shape[1:])
        return l_blocks_map

    def left_multiply(self, mats):
        """
        Left Multiplication.
        Currently only used for multiplying R obtained from right-canonicalization/compression.

        Args:
            mats : dict(q_label_r -> numpy.ndarray)
                The R matrix for each right-index quantum label.
        """
        blocks = []
        for block in self.blocks:
            q_label_r = block.q_labels[0]
            if q_label_r in mats:
                mat = np.tensordot(
                    mats[q_label_r], block.reduced, axes=([1], [0]))
                blocks.append(SubTensor(q_labels=block.q_labels, reduced=mat))
        self.blocks = blocks

    def right_multiply(self, mats):
        """
        Right Multiplication.
        Currently only used for multiplying L obtained from right-canonicalization/compression.

        Args:
            mats : dict(q_label_l -> numpy.ndarray)
                The L matrix for each left-index quantum label.
        """
        blocks = []
        for block in self.blocks:
            q_label_l = block.q_labels[-1]
            if q_label_l in mats:
                mat = np.tensordot(
                    block.reduced, mats[q_label_l], axes=([block.rank - 1], [0]))
                blocks.append(SubTensor(q_labels=block.q_labels, reduced=mat))
        self.blocks = blocks

    def truncate_singular_values(self, svd_s, k=-1, cutoff=0.0):
        """
        Internal method for truncation.

        Args:
            svd_s : list(numpy.ndarray)
                Singular value array for each quantum number.
            k : int
                Maximal total bond dimension.
                If `k == -1`, no restriction in total bond dimension.
            cutoff : double
                Minimal kept singluar value.

        Returns:
            svd_r : list(numpy.ndarray)
                Truncated list of singular value arrays.
            gls : list(numpy.ndarray)
                List of kept singular value indices.
            error : double
                Truncation error (same unit as singular value).
        """
        ss = [(i, j, v) for i, ps in enumerate(svd_s)
              for j, v in enumerate(ps)]
        ss.sort(key=lambda x: -x[2])
        ss_trunc = [x for x in ss if x[2] >= cutoff]
        ss_trunc = ss_trunc[:k] if k != -1 else ss_trunc
        ss_trunc.sort(key=lambda x: (x[0], x[1]))
        svd_r = [None] * len(svd_s)
        gls = [None] * len(svd_s)
        error = 0.0
        for ik, g in groupby(ss_trunc, key=lambda x: x[0]):
            gl = np.array([ig[1] for ig in g], dtype=int)
            gl_inv = np.array(
                list(set(range(0, len(svd_s[ik]))) - set(gl)), dtype=int)
            gls[ik] = gl
            error += (svd_s[ik][gl_inv] ** 2).sum()
            svd_r[ik] = svd_s[ik][gl]
        for ik in range(len(svd_s)):
            if gls[ik] is None:
                error += (svd_s[ik] ** 2).sum()
        return svd_r, gls, np.sqrt(error)

    def left_compress(self, k=-1, cutoff=0.0):
        """
        Left compression needs to collect all left indices for each specific right index.
        Bond dimension of rightmost index is compressed.

        Args:
            k : int
                Maximal total bond dimension.
                If `k == -1`, no restriction in total bond dimension.
            cutoff : double
                Minimal kept singluar value.

        Returns:
            compressed tensor, dict of right blocks, compression error
        """
        collected_rows = {}
        for block in self.blocks:
            q_label_r = block.q_labels[-1]
            if q_label_r not in collected_rows:
                collected_rows[q_label_r] = []
            collected_rows[q_label_r].append(block)
        svd_s, blocks_l, blocks_r = [], [], []
        for q_label_r, blocks in collected_rows.items():
            l_shapes = [np.prod(b.reduced.shape[:-1]) for b in blocks]
            mat = np.concatenate([b.reduced.reshape((sh, -1))
                                  for sh, b in zip(l_shapes, blocks)], axis=0)
            u, s, vh = np.linalg.svd(mat, full_matrices=False)
            svd_s.append(s)
            blocks_l.append(u)
            blocks_r.append(vh)
        svd_r, gls, error = self.truncate_singular_values(svd_s, k, cutoff)
        for ik, gl in enumerate(gls):
            if gl is not None and len(gl) != len(svd_s[ik]):
                blocks_l[ik] = blocks_l[ik][:, gl]
                blocks_r[ik] = blocks_r[ik][gl, :]
        r_blocks_map = {}
        l_blocks = []
        for ik, (q_label_r, blocks) in enumerate(collected_rows.items()):
            if svd_r[ik] is not None:
                l_shapes = [np.prod(b.reduced.shape[:-1]) for b in blocks]
                qs = np.split(blocks_l[ik], list(
                    accumulate(l_shapes[:-1])), axis=0)
                for q, b in zip(qs, blocks):
                    mat = q.reshape(
                        b.reduced.shape[:-1] + (blocks_l[ik].shape[1], ))
                    l_blocks.append(
                        SubTensor(q_labels=b.q_labels, reduced=mat))
                r_blocks_map[q_label_r] = svd_r[ik][:, None] * blocks_r[ik]
        return SparseTensor(blocks=l_blocks, ftype=self.ftype), r_blocks_map, error

    def right_compress(self, k=-1, cutoff=0.0):
        """
        Right compression needs to collect all right indices for each specific left index.
        Bond dimension of leftmost index is compressed.

        Args:
            k : int
                Maximal total bond dimension.
                If `k == -1`, no restriction in total bond dimension.
            cutoff : double
                Minimal kept singluar value.

        Returns:
            compressed tensor, dict of left blocks, compression error
        """
        collected_cols = {}
        for block in self.blocks:
            q_label_l = block.q_labels[0]
            if q_label_l not in collected_cols:
                collected_cols[q_label_l] = []
            collected_cols[q_label_l].append(block)
        svd_s, blocks_l, blocks_r = [], [], []
        for q_label_l, blocks in collected_cols.items():
            r_shapes = [np.prod(b.reduced.shape[1:]) for b in blocks]
            mat = np.concatenate([b.reduced.reshape((-1, sh))
                                  for sh, b in zip(r_shapes, blocks)], axis=1)
            u, s, vh = np.linalg.svd(mat, full_matrices=False)
            svd_s.append(s)
            blocks_l.append(u)
            blocks_r.append(vh)
        svd_r, gls, error = self.truncate_singular_values(svd_s, k, cutoff)
        for ik, gl in enumerate(gls):
            if gl is not None and len(gl) != len(svd_s[ik]):
                blocks_l[ik] = blocks_l[ik][:, gl]
                blocks_r[ik] = blocks_r[ik][gl, :]
        l_blocks_map = {}
        r_blocks = []
        for ik, (q_label_l, blocks) in enumerate(collected_cols.items()):
            if svd_r[ik] is not None:
                r_shapes = [np.prod(b.reduced.shape[1:]) for b in blocks]
                qs = np.split(blocks_r[ik], list(
                    accumulate(r_shapes[:-1])), axis=1)
                assert(len(qs) == len(blocks))
                for q, b in zip(qs, blocks):
                    mat = q.reshape(
                        (blocks_r[ik].shape[0], ) + b.reduced.shape[1:])
                    r_blocks.append(
                        SubTensor(q_labels=b.q_labels, reduced=mat))
                l_blocks_map[q_label_l] = svd_r[ik][None, :] * blocks_l[ik]
        return SparseTensor(blocks=r_blocks, ftype=self.ftype), l_blocks_map, error

    def __mul__(self, other):
        """Scalar multiplication."""
        return SparseTensor(blocks=[block * other for block in self.blocks], ftype=self.ftype)

    def __neg__(self):
        """Times (-1)."""
        return SparseTensor(blocks=[-block for block in self.blocks])

    def __repr__(self):
        return "\n".join("%3d %r" % (ib, b) for ib, b in enumerate(self.blocks))
