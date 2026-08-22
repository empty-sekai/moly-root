"""Quaternion and small-matrix helpers.

Conventions: quaternions are (x, y, z, w) tuples, matrices are column-major
flat lists of 16 floats (the glTF layout).
"""
import math


def qmul(a, b):
    """Hamilton product a·b, xyzw layout."""
    return (a[3]*b[0] + a[0]*b[3] + a[1]*b[2] - a[2]*b[1],
            a[3]*b[1] - a[0]*b[2] + a[1]*b[3] + a[2]*b[0],
            a[3]*b[2] + a[0]*b[1] - a[1]*b[0] + a[2]*b[3],
            a[3]*b[3] - a[0]*b[0] - a[1]*b[1] - a[2]*b[2])


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def qnorm(q):
    n = math.sqrt(sum(x * x for x in q))
    return tuple(x / n for x in q)


def qrotv(q, v):
    """Rotate vector v by quaternion q."""
    t = qmul(qmul(q, (v[0], v[1], v[2], 0.0)), qconj(q))
    return (t[0], t[1], t[2])


def axis_angle(axis, rad):
    n = math.sqrt(sum(c * c for c in axis))
    if n < 1e-12 or abs(rad) < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    s = math.sin(rad / 2) / n
    return (axis[0]*s, axis[1]*s, axis[2]*s, math.cos(rad / 2))


def qdist(a, b):
    """Distance between two rotations: component distance up to sign
    (q and -q represent the same rotation)."""
    d1 = math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(4)))
    d2 = math.sqrt(sum((a[i] + b[i]) ** 2 for i in range(4)))
    return min(d1, d2)


def trs_matrix(t, q, s):
    """Column-major 4x4 from translation, rotation (xyzw), scale."""
    x, y, z, w = q
    xx, yy, zz = x*x, y*y, z*z
    xy, xz, yz, wx, wy, wz = x*y, x*z, y*z, w*x, w*y, w*z
    r = [[1-2*(yy+zz), 2*(xy-wz),   2*(xz+wy)],
         [2*(xy+wz),   1-2*(xx+zz), 2*(yz-wx)],
         [2*(xz-wy),   2*(yz+wx),   1-2*(xx+yy)]]
    return [r[0][0]*s[0], r[1][0]*s[0], r[2][0]*s[0], 0.0,
            r[0][1]*s[1], r[1][1]*s[1], r[2][1]*s[1], 0.0,
            r[0][2]*s[2], r[1][2]*s[2], r[2][2]*s[2], 0.0,
            t[0], t[1], t[2], 1.0]


def mat_mul(a, b):
    """Column-major 4x4 product a·b."""
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c*4+r] = sum(a[k*4+r] * b[c*4+k] for k in range(4))
    return out


def mat_inv(m):
    """General 4x4 inverse via Gauss-Jordan (robust for TRS matrices)."""
    n = 4
    A = [[m[c*4+r] for c in range(n)] for r in range(n)]
    I = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for i in range(n):
        p = max(range(i, n), key=lambda k: abs(A[k][i]))
        if abs(A[p][i]) < 1e-12:
            raise ValueError("singular matrix")
        A[i], A[p] = A[p], A[i]
        I[i], I[p] = I[p], I[i]
        d = A[i][i]
        A[i] = [v/d for v in A[i]]
        I[i] = [v/d for v in I[i]]
        for k in range(n):
            if k == i:
                continue
            f = A[k][i]
            if f:
                A[k] = [av - f*bv for av, bv in zip(A[k], A[i])]
                I[k] = [av - f*bv for av, bv in zip(I[k], I[i])]
    return [I[r][c] for c in range(n) for r in range(n)]
