// imageprobe measures a local ONNX image model against prepared float32 tensors.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"net"
	"net/netip"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"runtime/debug"
	"strings"
	"syscall"
	"time"

	"github.com/osint-builders/pipelines/cli/internal/imageembedding"
	"github.com/osint-builders/pipelines/cli/internal/imagepreprocess"
)

func main() {
	if err := run(os.Args[1:], os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(args []string, stdout io.Writer) error {
	processStarted := time.Now()
	flags := flag.NewFlagSet("imageprobe", flag.ContinueOnError)
	var spec imageembedding.Spec
	canaryAddress := flags.String("network-canary", "", "isolated TCP policy check at numeric IP:port; cannot combine with other flags")
	filename := flags.String("model", "", "local self-contained ONNX model")
	manifestFile := flags.String("manifest", "", "model manifest; resolves the sibling model and preprocessing recipe")
	tensorFile := flags.String("tensor", "", "little-endian float32 RGB CHW tensor")
	imageFile := flags.String("image", "", "JPEG or PNG; requires --manifest")
	repeats := flags.Int("repeats", 3, "repeated requests after the first")
	flags.StringVar(&spec.ModelSHA256, "sha256", "", "expected model SHA-256")
	flags.StringVar(&spec.InputName, "input", "pixel_values", "ONNX input name")
	flags.StringVar(&spec.OutputName, "output", "image_features", "ONNX embedding output name")
	flags.IntVar(&spec.Height, "height", 256, "input height")
	flags.IntVar(&spec.Width, "width", 256, "input width")
	flags.IntVar(&spec.Dimensions, "dimensions", 512, "embedding dimensions")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *canaryAddress != "" {
		conflict := flags.NArg() != 0
		flags.Visit(func(f *flag.Flag) {
			if f.Name != "network-canary" {
				conflict = true
			}
		})
		if conflict {
			return errors.New("network canary cannot be combined with other flags or arguments")
		}
		return networkCanary(*canaryAddress, stdout, net.DialTimeout)
	}
	if flags.NArg() != 0 || (*tensorFile == "") == (*imageFile == "") || *repeats < 0 || *repeats > 100 {
		return errors.New("provide exactly one tensor or image path, and between 0 and 100 repeats")
	}
	var recipe imagepreprocess.Recipe
	if *manifestFile != "" {
		var conflict bool
		flags.Visit(func(f *flag.Flag) {
			if f.Name != "manifest" && f.Name != "tensor" && f.Name != "image" && f.Name != "repeats" {
				conflict = true
			}
		})
		if conflict {
			return errors.New("manifest cannot be combined with model specification flags")
		}
		var err error
		*filename, spec, recipe, err = readManifest(*manifestFile)
		if err != nil {
			return err
		}
	}
	if *filename == "" || (*imageFile != "" && *manifestFile == "") {
		return errors.New("provide --model and --sha256 for a tensor, or --manifest for an image")
	}
	if spec.Height < 1 || spec.Height > 4096 || spec.Width < 1 || spec.Width > 4096 {
		return errors.New("input dimensions must be between 1 and 4096")
	}
	prepare := func() ([]float32, error) {
		if *imageFile != "" {
			contents, err := readLimited(*imageFile, imagepreprocess.MaxImageBytes)
			if err != nil {
				return nil, err
			}
			return imagepreprocess.Preprocess(contents, recipe)
		}
		length := 4 * 3 * spec.Height * spec.Width
		contents, err := readLimited(*tensorFile, length)
		if err != nil {
			return nil, err
		}
		if len(contents) != length {
			return nil, errors.New("tensor file size does not match the input shape")
		}
		tensor := make([]float32, len(contents)/4)
		for i := range tensor {
			tensor[i] = math.Float32frombits(binary.LittleEndian.Uint32(contents[4*i:]))
		}
		return tensor, nil
	}
	preprocessStarted := time.Now()
	tensor, err := prepare()
	if err != nil {
		return err
	}
	preprocessMS := milliseconds(time.Since(preprocessStarted))
	hash := sha256.New()
	if err := binary.Write(hash, binary.LittleEndian, tensor); err != nil {
		return err
	}
	started := time.Now()
	encoder, err := imageembedding.New(context.Background(), *filename, spec)
	if err != nil {
		return err
	}
	defer encoder.Close()
	loadMS := milliseconds(time.Since(started))
	started = time.Now()
	result, err := encoder.Encode(context.Background(), tensor)
	if err != nil {
		return err
	}
	firstMS := milliseconds(time.Since(started))
	totalFirstMS := milliseconds(time.Since(processStarted))
	repeatMS := make([]float64, *repeats)
	for i := range repeatMS {
		started = time.Now()
		tensor, err = prepare()
		if err != nil {
			return err
		}
		_, err = encoder.Encode(context.Background(), tensor)
		if err != nil {
			return err
		}
		repeatMS[i] = milliseconds(time.Since(started))
	}
	var memory runtime.MemStats
	runtime.ReadMemStats(&memory)
	settings := map[string]string{}
	modules := map[string]string{}
	if build, ok := debug.ReadBuildInfo(); ok {
		for _, setting := range build.Settings {
			settings[setting.Key] = setting.Value
		}
		for _, dependency := range build.Deps {
			modules[dependency.Path] = dependency.Version
		}
	}
	return json.NewEncoder(stdout).Encode(struct {
		Spec imageembedding.Spec `json:"spec"`
		imageembedding.Result
		TensorSHA256 string            `json:"tensor_sha256"`
		PreprocessMS float64           `json:"preprocess_ms"`
		LoadMS       float64           `json:"load_ms"`
		FirstMS      float64           `json:"first_ms"`
		TotalFirstMS float64           `json:"total_first_ms"`
		RepeatMS     []float64         `json:"repeat_ms"`
		GoVersion    string            `json:"go_version"`
		GOOS         string            `json:"goos"`
		GOARCH       string            `json:"goarch"`
		GOMAXPROCS   int               `json:"gomaxprocs"`
		HeapBytes    uint64            `json:"heap_bytes"`
		GoSysBytes   uint64            `json:"go_sys_bytes"`
		Build        map[string]string `json:"build"`
		Modules      map[string]string `json:"modules"`
	}{spec, result, hex.EncodeToString(hash.Sum(nil)), preprocessMS, loadMS, firstMS, totalFirstMS, repeatMS,
		runtime.Version(), runtime.GOOS, runtime.GOARCH, runtime.GOMAXPROCS(0), memory.HeapAlloc, memory.Sys, settings, modules})
}

// This explicit mode is the only network operation in the probe. It lets the
// checker distinguish an OS policy rejection from an unavailable destination.
func networkCanary(address string, stdout io.Writer, dial func(string, string, time.Duration) (net.Conn, error)) error {
	target, err := netip.ParseAddrPort(address)
	if err != nil || target.Port() == 0 {
		return errors.New("network canary requires a numeric IP address and a nonzero numeric port")
	}
	result := struct {
		Address      string `json:"address"`
		Reachable    bool   `json:"reachable"`
		PolicyDenied bool   `json:"policy_denied"`
		Timeout      bool   `json:"timeout"`
		Errno        uint64 `json:"errno,omitempty"`
		Error        string `json:"error,omitempty"`
	}{Address: address}
	connection, err := dial("tcp", address, 5*time.Second)
	if err == nil {
		result.Reachable = true
		_ = connection.Close()
	} else {
		result.PolicyDenied = errors.Is(err, syscall.EPERM) || errors.Is(err, syscall.EACCES) || errors.Is(err, syscall.Errno(10013))
		var networkError net.Error
		result.Timeout = errors.Is(err, os.ErrDeadlineExceeded) || errors.Is(err, context.DeadlineExceeded) ||
			(errors.As(err, &networkError) && networkError.Timeout())
		var errno syscall.Errno
		if errors.As(err, &errno) {
			result.Errno = uint64(errno)
		}
		result.Error = err.Error()
	}
	return json.NewEncoder(stdout).Encode(result)
}

func milliseconds(duration time.Duration) float64 {
	return float64(duration) / float64(time.Millisecond)
}

func readLimited(filename string, limit int) ([]byte, error) {
	file, err := os.Open(filename)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	contents, err := io.ReadAll(io.LimitReader(file, int64(limit)+1))
	if err != nil {
		return nil, err
	}
	if len(contents) > limit {
		return nil, errors.New("input file exceeds expected byte limit")
	}
	return contents, nil
}

func readManifest(filename string) (string, imageembedding.Spec, imagepreprocess.Recipe, error) {
	var manifest struct {
		SchemaVersion int                    `json:"schema_version"`
		File          string                 `json:"file"`
		SHA256        string                 `json:"sha256"`
		Bytes         int64                  `json:"bytes"`
		Input         string                 `json:"input"`
		Output        string                 `json:"output"`
		Shape         []int                  `json:"shape"`
		Dimensions    int                    `json:"dimensions"`
		Normalization string                 `json:"normalization"`
		Preprocess    imagepreprocess.Recipe `json:"preprocess"`
	}
	contents, err := readLimited(filename, 1<<20)
	if err == nil {
		err = json.Unmarshal(contents, &manifest)
	}
	var spec imageembedding.Spec
	if err != nil {
		return "", spec, manifest.Preprocess, err
	}
	if manifest.SchemaVersion != 1 || manifest.Normalization != "l2" ||
		!regexp.MustCompile(`^[a-zA-Z0-9_.-]+\.onnx$`).MatchString(manifest.File) ||
		manifest.Bytes < 1 || manifest.Bytes > 512<<20 || len(manifest.Shape) != 4 ||
		manifest.Shape[0] != 1 || manifest.Shape[1] != 3 ||
		manifest.Shape[2] != manifest.Preprocess.Size || manifest.Shape[3] != manifest.Preprocess.Size {
		return "", spec, manifest.Preprocess, errors.New("unsupported image model manifest")
	}
	if err := manifest.Preprocess.Validate(); err != nil {
		return "", spec, manifest.Preprocess, err
	}
	spec = imageembedding.Spec{ModelSHA256: manifest.SHA256, InputName: manifest.Input, OutputName: manifest.Output,
		Height: manifest.Shape[2], Width: manifest.Shape[3], Dimensions: manifest.Dimensions}
	directory, err := filepath.Abs(filepath.Dir(filename))
	if err != nil {
		return "", spec, manifest.Preprocess, err
	}
	directory, err = filepath.EvalSymlinks(directory)
	if err != nil {
		return "", spec, manifest.Preprocess, err
	}
	modelPath, err := filepath.EvalSymlinks(filepath.Join(directory, manifest.File))
	if err != nil {
		return "", spec, manifest.Preprocess, err
	}
	relative, err := filepath.Rel(directory, modelPath)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", spec, manifest.Preprocess, errors.New("image model path escapes manifest directory")
	}
	info, err := os.Stat(modelPath)
	if err != nil {
		return "", spec, manifest.Preprocess, err
	}
	if info.Size() != manifest.Bytes {
		return "", spec, manifest.Preprocess, errors.New("image model size does not match manifest")
	}
	return modelPath, spec, manifest.Preprocess, nil
}
