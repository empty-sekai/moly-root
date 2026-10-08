# Third-party notices

## sssekai

The animation decoder in `chara.mecanim.clip` was written independently: its
data structures, curve indexing, frame decoding, and evaluation code are our
own design, and no sssekai code is included in this repository.

However, the golden vectors this decoder was validated against were produced
by a test oracle adapted from `sssekai.unity.AnimationClip` (version 0.8.0,
MIT, (c) 2024 mos9527). The oracle itself is not distributed here, but the
development process benefited from sssekai, so its MIT license text is
reproduced below in good faith.

moly-root's AssetBundle decryption implementation is independently written.
The Japanese-server protocol framing, endpoint construction, guest-session
sequence, and manifest URL shape were adapted from sssekai 0.8.0 at upstream
commit `523a3659ea6641a6aa9c5f940341ad458391344c`; moly-root does not invoke its
command-line program or import it as a library. Consumers can also supply an
offline manifest descriptor and perform no network discovery.

### sssekai MIT License

Copyright (c) 2024 mos9527

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## sssekai AssetBundle decryption

The AssetBundle decryption in `core.fetch` is algorithmically equivalent to
`sssekai` `crypto/AssetBundle.py::decrypt_iter`: it removes the four-byte
wrapper and inverts five bytes in each eight-byte block of the first 128-byte
region. This implementation was written independently and is not a line-for-
line port. The comparison reference is sssekai commit
`523a3659ea6641a6aa9c5f940341ad458391344c`, under the MIT license reproduced
above.

## three.js r160

The browser viewer includes `three.module.min.js`, `GLTFLoader.js`,
`OrbitControls.js`, and `BufferGeometryUtils.js` from Three.js r160. Three.js
is distributed under the MIT License. The license text follows.

### Three.js MIT License

Copyright (c) 2010-2023 three.js authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
