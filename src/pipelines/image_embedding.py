"""Checksum-pinned, offline image encoding with a shared pixel recipe."""

import hashlib
import json
import re
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from pipelines.image_preprocess import Recipe, preprocess


class Encoder:
    def __init__(self, directory: Path, manifest: dict) -> None:
        import onnxruntime as ort

        name = manifest.get("file", "")
        checksum = manifest.get("sha256", "")
        dimensions = manifest.get("dimensions")
        shape = manifest.get("shape")
        if (
            type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or type(manifest.get("bytes")) is not int
            or type(shape) is not list
            or any(type(value) is not int for value in shape)
            or not isinstance(name, str)
            or not re.fullmatch(r"[a-zA-Z0-9_.-]+\.onnx", name)
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or type(dimensions) is not int
            or not 1 <= dimensions <= 4096
            or manifest.get("normalization") != "l2"
        ):
            raise ValueError("Unsupported image model manifest")
        try:
            self.recipe = Recipe(**manifest["preprocess"])
            self.recipe.validate()
        except (KeyError, TypeError) as error:
            raise ValueError("Invalid image model preprocessing") from error
        self.shape = [1, 3, self.recipe.size, self.recipe.size]
        if shape != self.shape:
            raise ValueError("Model shape does not match image preprocessing")
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Image model path escapes directory")
        if (
            path.stat().st_size != manifest.get("bytes")
            or path.stat().st_size > 512 * 1024**2
        ):
            raise ValueError("Image model size mismatch")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != checksum:
            raise ValueError("Image model checksum mismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            body, options, providers=["CPUExecutionProvider"]
        )
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if (
            len(inputs) != 1
            or len(outputs) != 1
            or inputs[0].name != manifest.get("input")
            or inputs[0].type != "tensor(float)"
            or inputs[0].shape != self.shape
            or outputs[0].name != manifest.get("output")
            or outputs[0].type != "tensor(float)"
            or outputs[0].shape != [1, dimensions]
        ):
            raise ValueError("Image graph does not match pinned interface")
        self.manifest = manifest
        self.dimensions = dimensions
        self.input = inputs[0].name
        self.output = outputs[0].name

    @classmethod
    def from_manifest(cls, path: Path) -> "Encoder":
        return cls(path.parent, json.loads(path.read_text(encoding="utf-8")))

    def encode_tensor(self, tensor: NDArray[np.float32]) -> NDArray[np.float32]:
        if (
            tensor.dtype != np.float32
            or list(tensor.shape) != self.shape[1:]
            or not np.isfinite(tensor).all()
        ):
            raise ValueError("Expected a finite float32 CHW image tensor")
        raw = self.session.run([self.output], {self.input: tensor[np.newaxis]})[0]
        if raw.shape != (1, self.dimensions) or not np.isfinite(raw).all():
            raise ValueError("Invalid image embedding output")
        norm = float(np.linalg.norm(raw.astype(np.float64)))
        if not np.isfinite(norm) or norm <= 1e-12:
            raise ValueError("Image encoder returned an empty embedding")
        return (raw[0].astype(np.float64) / norm).astype(np.float32)

    def encode(self, encoded: bytes) -> NDArray[np.float32]:
        return self.encode_tensor(preprocess(encoded, self.recipe))
