import json
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
import hashlib

from PIL import Image
import tempfile

import numpy as np
from deepface import DeepFace


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "settings.json"
FRAMES_DIR = BASE_DIR / "frames"
KNOWN_DIR = BASE_DIR / "known_faces"


_CACHE_LOCK = threading.Lock()
_REFERENCE_CACHE: list[dict] | None = None


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)

def get_data_dir(config: dict) -> Path:
    data_dir = config.get("data_dir", "data")
    return BASE_DIR / data_dir

def get_embedding_cache_dir(config: dict) -> Path:
    cache_dir = config.get("embedding_cache_dir")

    if cache_dir:
        return BASE_DIR / cache_dir

    return get_data_dir(config) / "embeddings"

def get_reference_fingerprint(reference_path: Path, config: dict) -> str:
    """
    Erzeugt einen stabilen Cache-Key.
    Enthält Pfad, Änderungszeit, Dateigröße, Modell und Detector-Konfiguration.
    Wenn Bild, Modell oder Detector geändert wird, entsteht automatisch ein neuer Cache-Key.
    """
    stat = reference_path.stat()

    model_name = str(config.get("deepface_model", "SFace"))

    detectors = config.get("deepface_detectors")
    if isinstance(detectors, list) and detectors:
        detector_backend = ",".join(str(detector) for detector in detectors)
    else:
        detector_backend = str(config.get("deepface_detector", "opencv"))

    raw = "|".join(
        [
            str(reference_path.resolve()),
            str(stat.st_mtime_ns),
            str(stat.st_size),
            model_name,
            detector_backend,
        ]
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def get_embedding_cache_path(reference_path: Path, person: str, config: dict) -> Path:
    cache_dir = get_embedding_cache_dir(config) / person
    cache_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = get_reference_fingerprint(reference_path, config)
    return cache_dir / f"{reference_path.stem}_{fingerprint[:16]}.npy"


def load_cached_embedding(reference_path: Path, person: str, config: dict) -> Optional[np.ndarray]:
    if not config.get("embedding_cache_enabled", True):
        return None

    cache_path = get_embedding_cache_path(reference_path, person, config)

    if not cache_path.exists():
        return None

    try:
        embedding = np.load(cache_path)
        return np.array(embedding, dtype=np.float32)
    except Exception as error:
        print(f"  Embedding-Cache konnte nicht geladen werden: {cache_path.name} ({error})")
        return None


def save_cached_embedding(reference_path: Path, person: str, config: dict, embedding: np.ndarray) -> None:
    if not config.get("embedding_cache_enabled", True):
        return

    cache_path = get_embedding_cache_path(reference_path, person, config)

    try:
        np.save(cache_path, embedding)
    except Exception as error:
        print(f"  Embedding-Cache konnte nicht gespeichert werden: {cache_path.name} ({error})")

def get_people() -> dict[str, Path]:
    people = {}

    if not KNOWN_DIR.exists():
        return people

    for person_dir in KNOWN_DIR.iterdir():
        if person_dir.is_dir():
            people[person_dir.name.lower()] = person_dir

    return people


def get_reference_images(person_dir: Path) -> List[Path]:
    if not person_dir.exists():
        return []

    files = []
    files.extend(person_dir.glob("*.jpg"))
    files.extend(person_dir.glob("*.jpeg"))
    files.extend(person_dir.glob("*.png"))

    return sorted(files)


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    a_norm = np.linalg.norm(a)
    b_norm = np.linalg.norm(b)

    if a_norm == 0 or b_norm == 0:
        return 999.0

    return float(1.0 - np.dot(a, b) / (a_norm * b_norm))


def get_detector_backends(config: dict) -> list[str]:
    detectors = config.get("deepface_detectors")

    if isinstance(detectors, list) and detectors:
        return [str(detector) for detector in detectors]

    return [str(config.get("deepface_detector", "opencv"))]

def prepare_image_for_detection(image_path: Path, config: dict) -> Path:
    max_width = int(config.get("max_detection_image_width", 1280))

    try:
        with Image.open(image_path) as img:
            width, height = img.size

            if width <= max_width:
                return image_path

            ratio = max_width / width
            new_height = int(height * ratio)

            resized = img.convert("RGB").resize((max_width, new_height))

            temp_dir = get_data_dir(config) / "tmp_detection"
            temp_dir.mkdir(parents=True, exist_ok=True)

            temp_path = temp_dir / f"{image_path.stem}_detect_{max_width}.jpg"
            resized.save(temp_path, "JPEG", quality=92)

            return temp_path

    except Exception as error:
        print(f"  Bild konnte nicht für Detection vorbereitet werden: {image_path.name} ({error})")
        return image_path

def extract_best_embedding(
    image_path: Path,
    config: dict,
    enforce_detection: bool = True
) -> Optional[np.ndarray]:
    model_name = config.get("deepface_model", "SFace")
    debug = bool(config.get("debug_face_detection", False))
    
    last_error = None
    detection_image_path = prepare_image_for_detection(image_path, config)
    for detector_backend in get_detector_backends(config):
        try:
            representations = DeepFace.represent(
                img_path=str(detection_image_path),
                model_name=model_name,
                detector_backend=detector_backend,
                enforce_detection=enforce_detection,
                align=True,
            )

            if not representations:
                if debug:
                    print(f"  Detector {detector_backend}: keine Representation für {image_path.name}")
                continue

            best_representation = max(
                representations,
                key=lambda item: (
                    item.get("facial_area", {}).get("w", 0)
                    * item.get("facial_area", {}).get("h", 0)
                ),
            )

            embedding = best_representation.get("embedding")

            if embedding is None:
                if debug:
                    print(f"  Detector {detector_backend}: Embedding fehlt für {image_path.name}")
                continue

            if debug:
                area = best_representation.get("facial_area", {})
                print(
                    f"  Detector {detector_backend}: Gesicht erkannt in {image_path.name} "
                    f"area={area}"
                )

            return np.array(embedding, dtype=np.float32)

        except Exception as error:
            last_error = error

            if debug:
                print(
                    f"  Detector {detector_backend}: kein Gesicht/Fehler bei {image_path.name}: {error}"
                )

            continue

    if debug and last_error:
        print(f"  Alle Detectoren fehlgeschlagen für {image_path.name}. Letzter Fehler: {last_error}")

    return None


def build_reference_cache(config: dict) -> list[dict]:
    print("Baue DeepFace Referenz-Embedding-Cache auf...")

    people = get_people()
    cache = []

    loaded_from_disk = 0
    computed = 0
    skipped = 0

    for person, person_dir in people.items():
        references = get_reference_images(person_dir)

        for reference_path in references:
            embedding = load_cached_embedding(reference_path, person, config)

            if embedding is not None:
                loaded_from_disk += 1
                print(f"  Embedding geladen: {person}/{reference_path.name}")
            else:
                embedding = extract_best_embedding(reference_path, config, enforce_detection=True)

                if embedding is None:
                    skipped += 1
                    print(f"  Referenz übersprungen, kein Gesicht erkannt: {person}/{reference_path.name}")
                    continue

                save_cached_embedding(reference_path, person, config, embedding)
                computed += 1
                print(f"  Embedding berechnet: {person}/{reference_path.name}")

            cache.append(
                {
                    "person": person,
                    "path": str(reference_path),
                    "filename": reference_path.name,
                    "embedding": embedding,
                }
            )

    print(f"Referenz-Embeddings geladen: {len(cache)}")
    print(f"  aus Cache: {loaded_from_disk}")
    print(f"  neu berechnet: {computed}")
    print(f"  übersprungen: {skipped}")

    return cache


def get_reference_cache(config: dict) -> list[dict]:
    global _REFERENCE_CACHE

    if _REFERENCE_CACHE is not None:
        return _REFERENCE_CACHE

    with _CACHE_LOCK:
        if _REFERENCE_CACHE is None:
            _REFERENCE_CACHE = build_reference_cache(config)

    return _REFERENCE_CACHE


def warmup_recognizer() -> None:
    """
    Wird beim Start von mqtt_trigger.py aufgerufen.
    Lädt DeepFace-Modell und berechnet Referenz-Embeddings einmalig.
    """
    config = load_config()
    get_reference_cache(config)
    print("DeepFace Warmup fertig.")


def recognize_single_frame(frame_path: Path, config: Optional[dict] = None) -> dict:
    if config is None:
        config = load_config()

    threshold = float(config.get("deepface_distance_threshold", 0.593))
    references = get_reference_cache(config)

    if not references:
        return {
            "status": "error",
            "frame": frame_path.name,
            "subject": None,
            "similarity": 0.0,
            "distance": 999.0,
            "error": "Keine Referenz-Embeddings vorhanden.",
        }

    frame_embedding = extract_best_embedding(frame_path, config, enforce_detection=True)

    if frame_embedding is None:
        return {
            "status": "no_face",
            "frame": frame_path.name,
            "subject": None,
            "similarity": 0.0,
            "distance": 999.0,
        }

    best = None
    best_distance = 999.0

    for ref in references:
        distance = cosine_distance(frame_embedding, ref["embedding"])

        if distance < best_distance:
            best_distance = distance
            best = ref

    similarity = max(0.0, 1.0 - best_distance)

    if best and best_distance <= threshold:
        return {
            "status": "known",
            "frame": frame_path.name,
            "subject": best["person"],
            "similarity": similarity,
            "distance": best_distance,
            "threshold": threshold,
            "reference": best["filename"],
        }

    return {
        "status": "unknown",
        "frame": frame_path.name,
        "subject": None,
        "similarity": similarity,
        "distance": best_distance,
        "threshold": threshold,
        "best_candidate": best["person"] if best else None,
        "reference": best["filename"] if best else None,
    }


def recognize_all_frames(frames_dir: Path | None = None) -> dict:
    config = load_config()
    min_hits = int(config.get("min_hits", 1))

    if frames_dir is None:
        frames_dir = FRAMES_DIR

    frames = sorted(frames_dir.glob("frame_*.jpg"))

    if not frames:
        raise RuntimeError("Keine Frames gefunden.")

    hits: Dict[str, List[float]] = {}
    best_distances: Dict[str, float] = {}
    no_face_count = 0
    unknown_seen = False
    error_count = 0

    for frame in frames:
        print(f"Prüfe {frame.name}...")

        result = recognize_single_frame(frame, config)
        status = result["status"]

        if status == "known":
            person = result["subject"]
            similarity = float(result.get("similarity", 0.0))
            distance = float(result.get("distance", 999.0))

            hits.setdefault(person, []).append(similarity)

            if person not in best_distances or distance < best_distances[person]:
                best_distances[person] = distance

            print(
                f"  Treffer: {person} "
                f"distance={distance:.4f} "
                f"reference={result.get('reference')}"
            )

            if len(hits[person]) >= min_hits:
                avg_similarity = sum(hits[person]) / len(hits[person])

                return {
                    "status": "known",
                    "subject": person,
                    "hits": len(hits[person]),
                    "avg_similarity": avg_similarity,
                    "all_hits": {k: len(v) for k, v in hits.items()},
                    "best_distances": best_distances,
                }

        elif status == "no_face":
            no_face_count += 1
            print("  Kein Gesicht erkannt.")

        elif status == "unknown":
            unknown_seen = True
            print(
                f"  Gesicht erkannt, aber unbekannt. "
                f"Best={result.get('best_candidate')} "
                f"distance={float(result.get('distance', 999.0)):.4f}"
            )

        else:
            error_count += 1
            print(f"  Fehler: {result.get('error')}")

    if unknown_seen:
        return {
            "status": "unknown",
            "subject": None,
            "hits": max((len(v) for v in hits.values()), default=0),
            "avg_similarity": 0.0,
            "all_hits": {k: len(v) for k, v in hits.items()},
            "best_distances": best_distances,
            "no_face_count": no_face_count,
            "error_count": error_count,
        }

    if no_face_count > 0:
        return {
            "status": "no_face",
            "subject": None,
            "hits": 0,
            "avg_similarity": 0.0,
            "all_hits": {k: len(v) for k, v in hits.items()},
            "best_distances": best_distances,
            "no_face_count": no_face_count,
            "error_count": error_count,
        }

    return {
        "status": "error",
        "subject": None,
        "hits": 0,
        "avg_similarity": 0.0,
        "all_hits": {k: len(v) for k, v in hits.items()},
        "best_distances": best_distances,
        "no_face_count": no_face_count,
        "error_count": error_count,
    }


if __name__ == "__main__":
    import sys

    frames_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else FRAMES_DIR
    result = recognize_all_frames(frames_dir)

    print("\nERGEBNIS:")
    print(json.dumps(result, indent=2, ensure_ascii=False))