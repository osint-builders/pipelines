package embedding

import (
	"context"
	"io"
	"io/fs"
	"testing"
	"testing/fstest"
)

func TestEmbeddedModelFilesystemUsesRelativeParentsAndNeverWrites(t *testing.T) {
	files := readOnlyFS{fstest.MapFS{"model/model.onnx": {Data: []byte("weights")}, "model/sub/other.txt": {Data: []byte("metadata")}}}
	ctx := context.Background()
	found := false
	err := files.Walk(ctx, "s3://bundle/model", func(_ context.Context, _ string, parent string, info fs.FileInfo, reader io.Reader) (bool, error) {
		if info.Name() == "model.onnx" {
			found = true
			if parent != "" {
				t.Fatalf("expected relative parent, got %q", parent)
			}
			body, _ := io.ReadAll(reader)
			if string(body) != "weights" {
				t.Fatal(string(body))
			}
		}
		return true, nil
	})
	if err != nil || !found {
		t.Fatal(found, err)
	}
	if _, err := files.NewFileWriter(ctx, "model/cache", ""); err != fs.ErrPermission {
		t.Fatal(err)
	}
	if _, err := files.OpenFile(ctx, "s3://bundle/../../outside"); err == nil {
		t.Fatal("path escaped archive")
	}
}
