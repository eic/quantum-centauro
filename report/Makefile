.PHONY: all clean

all:
	latexmk -pdf -bibtex- -interaction=nonstopmode -halt-on-error -file-line-error -jobname=quantum_centauro_report main.tex

clean:
	latexmk -C -jobname=quantum_centauro_report
