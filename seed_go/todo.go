// Package todo is a small in-memory todo API.
package todo

import (
	"encoding/json"
	"net/http"
	"sync"
)

type Todo struct {
	ID    int    `json:"id"`
	Title string `json:"title"`
	Done  bool   `json:"done"`
}

type Store struct {
	mu    sync.Mutex
	next  int
	todos []Todo
}

func (s *Store) Add(title string) Todo {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.next++
	t := Todo{ID: s.next, Title: title}
	s.todos = append(s.todos, t)
	return t
}

func (s *Store) List() []Todo {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Todo(nil), s.todos...)
}

// Handler serves POST /todos and GET /todos.
func Handler(s *Store) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /todos", func(w http.ResponseWriter, r *http.Request) {
		var in struct {
			Title string `json:"title"`
		}
		if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
			http.Error(w, "invalid json", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(s.Add(in.Title))
	})
	mux.HandleFunc("GET /todos", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(s.List())
	})
	return mux
}
