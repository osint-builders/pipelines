// Package embedding runs the bundled sentence model without native libraries or network access.
package embedding

import (
	"context"
	"errors"
	"io"
	"io/fs"
	"path"
	"strings"

	"github.com/knights-analytics/hugot"
	"github.com/knights-analytics/hugot/backends"
	"github.com/knights-analytics/hugot/options"
	"github.com/knights-analytics/hugot/pipelines"
	"github.com/knights-analytics/hugot/util/fileutil"
)

type Encoder struct {
	session  *hugot.Session
	pipeline *pipelines.FeatureExtractionPipeline
}

func New(ctx context.Context, files fs.FS) (*Encoder, error) {
	session, err := hugot.NewGoSession(ctx, options.WithFileSystem(readOnlyFS{files}))
	if err != nil {
		return nil, err
	}
	pipeline, err := hugot.NewPipeline(session, hugot.FeatureExtractionConfig{
		// Hugot v0.7.8 selects its reader-based ONNX loader with this prefix.
		// The injected filesystem resolves it entirely inside the embedded ZIP.
		ModelPath: "s3://bundle/model", Name: "minilm", OnnxFilename: "model.onnx",
		Options: []backends.PipelineOption[*pipelines.FeatureExtractionPipeline]{pipelines.WithNormalization()},
	})
	if err != nil {
		_ = session.Destroy()
		return nil, err
	}
	return &Encoder{session, pipeline}, nil
}

func (e *Encoder) Close() error { return e.session.Destroy() }

func (e *Encoder) Encode(ctx context.Context, text string) ([]float32, error) {
	if len([]rune(text)) > 1000 {
		return nil, errors.New("query exceeds 1000 characters")
	}
	tk := e.pipeline.Model.Tokenizer.GoTokenizer.Tokenizer
	if len(tk.EncodeWithAnnotations(text).IDs) > 256 {
		return nil, errors.New("query exceeds 256 tokens")
	}
	result, err := e.pipeline.RunPipeline(ctx, []string{text})
	if err != nil {
		return nil, err
	}
	return result.Embeddings[0], nil
}

type readOnlyFS struct{ fs.FS }

func normalized(name string) string {
	return strings.TrimPrefix(strings.ReplaceAll(name, "\\", "/"), "s3://bundle/")
}
func (r readOnlyFS) OpenFile(_ context.Context, name string) (io.ReadCloser, error) {
	return r.Open(normalized(name))
}
func (r readOnlyFS) FileStats(_ context.Context, name string) (fs.FileInfo, error) {
	return fs.Stat(r.FS, normalized(name))
}
func (r readOnlyFS) FileExists(ctx context.Context, name string) (bool, error) {
	_, err := r.FileStats(ctx, name)
	if errors.Is(err, fs.ErrNotExist) {
		return false, nil
	}
	return err == nil, err
}
func (r readOnlyFS) Walk(ctx context.Context, name string, visit fileutil.OnVisit) error {
	name = normalized(name)
	return fs.WalkDir(r.FS, name, func(current string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		var reader io.ReadCloser
		if !entry.IsDir() {
			reader, err = r.Open(current)
			if err != nil {
				return err
			}
			defer reader.Close()
		}
		parent := strings.TrimPrefix(strings.TrimPrefix(path.Dir(current), name), "/")
		more, err := visit(ctx, current, parent, info, reader)
		if err != nil {
			return err
		}
		if !more {
			return fs.SkipAll
		}
		return nil
	})
}
func (r readOnlyFS) CopyFile(context.Context, string, string) error { return fs.ErrPermission }
func (r readOnlyFS) DeleteFile(context.Context, string) error       { return fs.ErrPermission }
func (r readOnlyFS) NewFileWriter(context.Context, string, string) (io.WriteCloser, error) {
	return nil, fs.ErrPermission
}
