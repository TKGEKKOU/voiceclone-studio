# Third-Party Notices

VoiceClone Flow includes integration code or small compatibility patches for the following projects. Runtime packages and model weights downloaded by users remain subject to their respective upstream licenses.

## GPT-SoVITS

Source: <https://github.com/RVC-Boss/GPT-SoVITS>

The files under `runtime_patches/GPT_SoVITS/` and `runtime_patches/runtime/` are compatibility patches derived from or intended for GPT-SoVITS and its runtime dependencies.

```text
MIT License

Copyright (c) 2024 RVC-Boss

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
```

## 7-Zip reduced command-line tool

Source: <https://www.7-zip.org/>

`runtime_patches/tools/7zr.exe` identifies itself as 7-Zip 26.02, copyright Igor Pavlov, Public domain. It is included only to extract the optional GPT-SoVITS archive on Windows.

## FFmpeg

Source: <https://ffmpeg.org/>

FFmpeg is downloaded at runtime and is not distributed in this repository. The selected build may be licensed under LGPL or GPL depending on its configuration; users should review the license bundled by the download provider.

## Demucs / HT-Demucs model

Source: <https://github.com/facebookresearch/demucs>

The optional ONNX separation model is downloaded at runtime and is not distributed in this repository. Its use remains subject to the model publisher's and upstream project's terms.
