package dataset

import (
	"math"
	"slices"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/text/unicode/norm"
)

type lexicalDocument struct {
	Entity                  int
	Text, EvidenceID, Field string
}

type lexicalHit struct {
	Entity int
	Score  float64
	Doc    int
	Terms  []string
	Reason string
}

type lexicalPosting struct {
	doc, frequency uint32
}

type lexicalTerm struct {
	postings []lexicalPosting
	idf      float64
}

type lexicalNameTerm struct {
	text  string
	runes []rune
}

type lexicalIndex struct {
	documents []lexicalDocument
	terms     map[string]*lexicalTerm
	names     map[int][]lexicalNameTerm
	norms     []float64
}

const (
	lexicalK1          = 1.2
	lexicalB           = 0.75
	lexicalTypoPenalty = 0.65
)

// newLexicalIndex keeps the caller's immutable document order for provenance.
func newLexicalIndex(documents []lexicalDocument) *lexicalIndex {
	index := &lexicalIndex{
		documents: documents,
		terms:     make(map[string]*lexicalTerm),
		names:     make(map[int][]lexicalNameTerm),
		norms:     make([]float64, len(documents)),
	}
	names := make(map[string]bool)
	var totalLength, documentCount float64
	for doc, document := range documents {
		tokens := lexicalTokens(document.Text)
		if len(tokens) == 0 {
			continue
		}
		index.norms[doc] = float64(len(tokens))
		totalLength += float64(len(tokens))
		documentCount++
		sort.Strings(tokens)
		for first := 0; first < len(tokens); {
			token := tokens[first]
			last := first + 1
			for last < len(tokens) && tokens[last] == token {
				last++
			}
			term := index.terms[token]
			if term == nil {
				term = &lexicalTerm{}
				index.terms[strings.Clone(token)] = term
			}
			term.postings = append(term.postings, lexicalPosting{uint32(doc), uint32(last - first)})
			if document.Field == "name" {
				names[token] = true
			}
			first = last
		}
	}
	if documentCount == 0 {
		return index
	}
	averageLength := totalLength / documentCount
	for doc, length := range index.norms {
		index.norms[doc] = lexicalK1 * (1 - lexicalB + lexicalB*length/averageLength)
	}
	for _, term := range index.terms {
		frequency := float64(len(term.postings))
		term.idf = math.Log1p((documentCount - frequency + 0.5) / (frequency + 0.5))
	}
	for text := range names {
		if runes := lexicalNameRunes(text); runes != nil {
			index.names[len(runes)] = append(index.names[len(runes)], lexicalNameTerm{text, runes})
		}
	}
	for _, terms := range index.names {
		sort.Slice(terms, func(i, j int) bool { return terms[i].text < terms[j].text })
	}
	return index
}

type lexicalContribution struct {
	score float64
	term  string
	fuzzy bool
}

type lexicalScore struct {
	score float64
	terms []string
	fuzzy bool
}

// search returns each entity's strongest document, never a sum across evidence.
func (index *lexicalIndex) search(query string) []lexicalHit {
	tokens := lexicalTokens(query)
	sort.Strings(tokens)
	scores := make([]lexicalScore, len(index.documents))
	for i, token := range tokens {
		if i > 0 && token == tokens[i-1] {
			continue
		}
		contributions := make(map[int]lexicalContribution)
		index.contribute(contributions, token, false)
		if runes := lexicalNameRunes(token); runes != nil {
			for length := len(runes) - 1; length <= len(runes)+1; length++ {
				for _, candidate := range index.names[length] {
					if candidate.text != token && lexicalOneEdit(runes, candidate.runes) {
						index.contribute(contributions, candidate.text, true)
					}
				}
			}
		}
		for doc, contribution := range contributions {
			scores[doc].score += contribution.score
			scores[doc].terms = append(scores[doc].terms, contribution.term)
			scores[doc].fuzzy = scores[doc].fuzzy || contribution.fuzzy
		}
	}
	best := make(map[int]lexicalHit)
	for doc, score := range scores {
		if score.score == 0 {
			continue
		}
		entity := index.documents[doc].Entity
		previous, exists := best[entity]
		if exists && previous.Score >= score.score {
			continue
		}
		reason := "lexical"
		if score.fuzzy {
			reason = "name_typo"
		}
		sort.Strings(score.terms)
		score.terms = slices.Compact(score.terms)
		best[entity] = lexicalHit{entity, score.score, doc, score.terms, reason}
	}
	hits := make([]lexicalHit, 0, len(best))
	for _, hit := range best {
		hits = append(hits, hit)
	}
	sort.Slice(hits, func(i, j int) bool {
		if hits[i].Score == hits[j].Score {
			return hits[i].Entity < hits[j].Entity
		}
		return hits[i].Score > hits[j].Score
	})
	return hits
}

func (index *lexicalIndex) contribute(scores map[int]lexicalContribution, token string, fuzzy bool) {
	term := index.terms[token]
	if term == nil {
		return
	}
	weight := term.idf * (lexicalK1 + 1)
	if fuzzy {
		weight *= lexicalTypoPenalty
	}
	for _, posting := range term.postings {
		doc := int(posting.doc)
		if fuzzy && index.documents[doc].Field != "name" {
			continue
		}
		frequency := float64(posting.frequency)
		score := weight * frequency / (frequency + index.norms[doc])
		previous, exists := scores[doc]
		if !exists || !fuzzy && previous.fuzzy || fuzzy == previous.fuzzy && score > previous.score {
			scores[doc] = lexicalContribution{score, token, fuzzy}
		}
	}
}

func lexicalTokens(text string) []string {
	text = norm.NFKD.String(strings.ToLower(text))
	tokens := make([]string, 0, len(text)/8+1)
	var token strings.Builder
	start, segment := -1, 0
	var hasLetter, hasDigit bool
	var previous rune
	flush := func(end int) {
		if start >= 0 {
			if token.Len() > 0 {
				token.WriteString(text[segment:end])
				tokens = append(tokens, token.String())
			} else {
				tokens = append(tokens, text[start:end])
			}
			token.Reset()
		}
		start = -1
		hasLetter, hasDigit = false, false
	}
	skip := func(position, size int) {
		if start >= 0 {
			token.WriteString(text[segment:position])
			segment = position + size
		}
	}
	for i, r := range text {
		switch {
		case unicode.IsMark(r):
			skip(i, utf8.RuneLen(r))
			continue
		case unicode.IsLetter(r):
			if start < 0 {
				start, segment = i, i
			}
			hasLetter = true
		case unicode.IsDigit(r):
			if start < 0 {
				start, segment = i, i
			}
			hasDigit = true
		case unicode.Is(unicode.Pd, r) || r == '\u2212':
			next, _ := utf8.DecodeRuneInString(text[i+utf8.RuneLen(r):])
			if hasLetter && (unicode.IsDigit(next) || hasDigit && unicode.IsLetter(next)) {
				skip(i, utf8.RuneLen(r))
			} else {
				flush(i)
			}
		case r == '.':
			next, _ := utf8.DecodeRuneInString(text[i+1:])
			if !unicode.IsDigit(previous) || !unicode.IsDigit(next) {
				flush(i)
			}
		default:
			flush(i)
		}
		previous = r
	}
	flush(len(text))
	return tokens
}

func lexicalNameRunes(token string) []rune {
	runes := []rune(token)
	if len(runes) < 5 {
		return nil
	}
	for _, r := range runes {
		if !unicode.IsLetter(r) {
			return nil
		}
	}
	return runes
}

func lexicalOneEdit(left, right []rune) bool {
	if len(left) > len(right) {
		left, right = right, left
	}
	if len(right)-len(left) > 1 {
		return false
	}
	for i := range left {
		if left[i] == right[i] {
			continue
		}
		if len(left) != len(right) {
			return lexicalEqualRunes(left[i:], right[i+1:])
		}
		if lexicalEqualRunes(left[i+1:], right[i+1:]) {
			return true
		}
		return i+1 < len(left) && left[i] == right[i+1] && left[i+1] == right[i] &&
			lexicalEqualRunes(left[i+2:], right[i+2:])
	}
	return true
}

func lexicalEqualRunes(left, right []rune) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}
	return true
}
