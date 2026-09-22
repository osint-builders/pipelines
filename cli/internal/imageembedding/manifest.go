package imageembedding

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"path"
	"regexp"

	"github.com/osint-builders/pipelines/cli/internal/imagepreprocess"
)

var modelFilename = regexp.MustCompile(`^[a-zA-Z0-9_.-]+\.onnx$`)

// NewFromFS loads a manifest and its self-contained graph from a read-only bundle.
func NewFromFS(ctx context.Context, files fs.FS, manifestPath string) (*Encoder, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if !fs.ValidPath(manifestPath) {
		return nil, errors.New("invalid image model manifest path")
	}
	file, err := files.Open(manifestPath)
	if err != nil {
		return nil, err
	}
	contents, err := io.ReadAll(io.LimitReader(file, (1<<20)+1))
	_ = file.Close()
	if err != nil {
		return nil, err
	}
	if len(contents) > 1<<20 {
		return nil, errors.New("image model manifest exceeds 1 MiB")
	}
	var manifest struct {
		SchemaVersion int                    `json:"schema_version"`
		File          string                 `json:"file"`
		SHA256        string                 `json:"sha256"`
		Bytes         int                    `json:"bytes"`
		Input         string                 `json:"input"`
		Output        string                 `json:"output"`
		Shape         []int                  `json:"shape"`
		Dimensions    int                    `json:"dimensions"`
		Normalization string                 `json:"normalization"`
		Preprocess    imagepreprocess.Recipe `json:"preprocess"`
	}
	if err := json.Unmarshal(contents, &manifest); err != nil {
		return nil, err
	}
	if manifest.SchemaVersion != 1 || manifest.Normalization != "l2" || !modelFilename.MatchString(manifest.File) ||
		manifest.Bytes < 1 || manifest.Bytes > maxModelBytes || len(manifest.Shape) != 4 ||
		manifest.Shape[0] != 1 || manifest.Shape[1] != 3 ||
		manifest.Shape[2] != manifest.Preprocess.Size || manifest.Shape[3] != manifest.Preprocess.Size {
		return nil, errors.New("unsupported image model manifest")
	}
	if err := manifest.Preprocess.Validate(); err != nil {
		return nil, err
	}
	spec := Spec{ModelSHA256: manifest.SHA256, InputName: manifest.Input, OutputName: manifest.Output,
		Height: manifest.Shape[2], Width: manifest.Shape[3], Dimensions: manifest.Dimensions}
	if err := validateSpec(spec); err != nil {
		return nil, err
	}
	graph, err := files.Open(path.Join(path.Dir(manifestPath), manifest.File))
	if err != nil {
		return nil, err
	}
	defer graph.Close()
	contents, err = io.ReadAll(io.LimitReader(graph, int64(manifest.Bytes)+1))
	if err != nil {
		return nil, err
	}
	if len(contents) != manifest.Bytes {
		return nil, errors.New("image model byte count mismatch")
	}
	encoder, err := newModel(ctx, contents, spec)
	if err != nil {
		return nil, err
	}
	encoder.recipe = manifest.Preprocess
	return encoder, nil
}
