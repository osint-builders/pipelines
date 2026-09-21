"""Export pinned MobileCLIP image towers for offline encoder evaluation.

Export environment: torch 2.14.0+cpu, torchvision 0.29.0+cpu, timm 1.0.29,
safetensors 0.8.0, onnx 1.23.0, x86_64 with AVX2/FMA3 and AVX2 ATen dispatch.
Native OpenCLIP dfndr2b preprocessing is
explicitly selected instead of the timm port's generic transform defaults.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import urllib.request
from pathlib import Path
from typing import Any, TypedDict


class Checkpoint(TypedDict):
    id: str
    architecture: str
    repository: str
    revision: str
    sha256: str
    bytes: int
    size: int


SOURCES: dict[str, Checkpoint] = {
    "mobileclip2-s0": {
        "id": "apple/MobileCLIP2-S0",
        "architecture": "fastvit_mci0",
        "repository": "timm/fastvit_mci0.apple_mclip2_dfndr2b",
        "revision": "577a2e992da2c7ba7cadc2a490bcfadfb2d69bce",
        "sha256": "70a995b7e7385126d0db94ee3d395d7fd3c251ce6d8dcd4f4c8d8fc7c29e3d04",
        "bytes": 45921320,
        "size": 256,
    },
    "mobileclip2-b": {
        "id": "apple/MobileCLIP2-B",
        "architecture": "vit_base_mci_224",
        "repository": "timm/vit_base_mci_224.apple_mclip2_dfndr2b",
        "revision": "c687c5da96d230248c18241bb76603f2cac5fb37",
        "sha256": "b6b1aabafe2d876fc65ebacfbbaae33fe6c87e26fecbfd7c4fb7fae5ba3f0b18",
        "bytes": 345414672,
        "size": 224,
    },
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checkpoint(directory: Path, name: str, *, download: bool) -> Path:
    spec = SOURCES[name]
    path = directory / f"{name}.safetensors"
    if not path.exists():
        if not download:
            raise ValueError(f"Missing checkpoint {path}; use --download to fetch it")
        url = (
            f"https://huggingface.co/{spec['repository']}/resolve/"
            f"{spec['revision']}/model.safetensors"
        )
        temporary = path.with_suffix(".part")
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                with temporary.open("wb") as output:
                    while body := response.read(1024 * 1024):
                        output.write(body)
                        if output.tell() > int(spec["bytes"]):
                            raise ValueError("Checkpoint exceeds pinned size")
            if (
                temporary.stat().st_size != spec["bytes"]
                or digest(temporary) != spec["sha256"]
            ):
                raise ValueError("Checkpoint checksum mismatch")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if path.stat().st_size != spec["bytes"] or digest(path) != spec["sha256"]:
        raise ValueError("Checkpoint checksum mismatch")
    return path


def compact_weights(original: Path, destination: Path) -> None:
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    model = onnx.load(original)
    casts = []
    names = {v.name for v in model.graph.initializer}
    for value in model.graph.initializer:
        if value.data_type != TensorProto.FLOAT:
            continue
        name = value.name
        storage_name = name + "__fp16_storage"
        if storage_name in names:
            raise ValueError("Conflicting compact weight name")
        weights = numpy_helper.to_array(value).astype(np.float16)
        if not np.isfinite(weights).all():
            raise ValueError("Weight cannot be represented as float16")
        value.CopyFrom(numpy_helper.from_array(weights, name=storage_name))
        casts.append(
            helper.make_node("Cast", [storage_name], [name], to=TensorProto.FLOAT)
        )
    nodes = [*casts, *model.graph.node]
    del model.graph.node[:]
    model.graph.node.extend(nodes)
    onnx.checker.check_model(model)
    onnx.save_model(model, destination)


def validate_export(actual: Any, expected: Any) -> dict:
    import numpy as np

    if (
        actual.shape != (1, 512)
        or expected.shape != (1, 512)
        or not np.isfinite(actual).all()
        or not np.isfinite(expected).all()
    ):
        raise ValueError("Export probe has invalid outputs")
    actual64, expected64 = actual.astype(np.float64), expected.astype(np.float64)
    actual_norm = float(np.linalg.norm(actual64))
    expected_norm = float(np.linalg.norm(expected64))
    if min(actual_norm, expected_norm) <= 1e-12:
        raise ValueError("Export probe returned an empty embedding")
    similarity = float(
        np.dot(actual64[0], expected64[0]) / (actual_norm * expected_norm)
    )
    if not np.isfinite(similarity) or similarity < 0.999:
        raise ValueError("Export probe does not match the source model")
    return {"cosine": similarity, "max_abs": float(np.max(np.abs(actual - expected)))}


def configure_export_cpu() -> dict[str, str]:
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("Model export requires an x86_64 CPU with AVX2/FMA3")
    # Reparameterization folds weights with ATen; pin its arithmetic before import.
    os.environ["ATEN_CPU_CAPABILITY"] = "avx2"
    import torch

    capabilities = torch.cpu.get_capabilities()
    if not all(capabilities.get(feature) for feature in ("avx2", "fma3")):
        raise ValueError("Model export requires an x86_64 CPU with AVX2/FMA3")
    if torch.backends.cpu.get_cpu_capability() != "AVX2":
        raise ValueError("Model export requires AVX2 dispatch; use a fresh process")
    return {"architecture": "x86_64", "aten_cpu_capability": "avx2"}


def prepare(directory: Path, name: str, *, download: bool = False) -> list[dict]:
    cpu = configure_export_cpu()

    import onnx
    import onnxruntime as ort
    import timm
    import torch
    from safetensors.torch import load_file
    from timm.utils import reparameterize_model

    from pipelines.image_preprocess import Recipe

    directory.mkdir(parents=True, exist_ok=True)
    path = checkpoint(directory, name, download=download)
    spec = SOURCES[name]
    size = int(spec["size"])
    torch.manual_seed(0)
    torch.set_num_threads(4)
    model = timm.create_model(
        str(spec["architecture"]), pretrained=False, num_classes=512
    )
    model.load_state_dict(load_file(str(path)))
    model = reparameterize_model(model.eval()).eval()
    probe = torch.linspace(0, 1, 3 * size * size).reshape(1, 3, size, size)
    with torch.no_grad():
        expected = model(probe).numpy()
    full = directory / f"{name}-fp32.onnx"
    torch.onnx.export(
        model,
        probe,
        str(full),
        input_names=["pixel_values"],
        output_names=["image_features"],
        opset_version=17,
        dynamo=False,
        external_data=False,
    )
    onnx.checker.check_model(str(full))
    variants = [("fp32", full)]
    if name == "mobileclip2-s0":
        compact = directory / f"{name}-fp16-storage.onnx"
        compact_weights(full, compact)
        variants.append(("fp16-storage", compact))
    manifests = []
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    for variant, graph in variants:
        session = ort.InferenceSession(
            str(graph), options, providers=["CPUExecutionProvider"]
        )
        actual = session.run(["image_features"], {"pixel_values": probe.numpy()})[0]
        agreement = validate_export(actual, expected)
        recipe = Recipe(size=size, resize_shortest_edge=size)
        manifest = {
            "schema_version": 1,
            "id": spec["id"],
            "variant": variant,
            "checkpoint": {k: v for k, v in spec.items() if k != "size"},
            "file": graph.name,
            "sha256": digest(graph),
            "bytes": graph.stat().st_size,
            "input": "pixel_values",
            "output": "image_features",
            "shape": [1, 3, size, size],
            "dimensions": 512,
            "normalization": "l2",
            "preprocess": vars(recipe),
            "export": {
                "cpu": cpu,
                "opset": 17,
                "reparameterized": True,
                "batch": 1,
                "tool": "tools/prepare_image_model.py",
                "torchscript_export": True,
                "packages": {
                    p: importlib.metadata.version(p)
                    for p in ("torch", "torchvision", "timm", "safetensors", "onnx")
                },
            },
            "export_probe": agreement,
        }
        graph.with_suffix(".json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        manifests.append(manifest)
    return manifests


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=list(SOURCES))
    parser.add_argument("--directory", type=Path, default=Path("build/m4/models"))
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    for manifest in prepare(args.directory, args.name, download=args.download):
        print(
            json.dumps(
                {
                    k: manifest[k]
                    for k in (
                        "id",
                        "variant",
                        "file",
                        "bytes",
                        "sha256",
                        "export_probe",
                    )
                }
            )
        )


if __name__ == "__main__":
    main()
