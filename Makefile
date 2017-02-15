
help:
	@echo
	@echo "test and install the firecloud command line tool"
	@echo
	@echo "Targets:"
	@echo
	@echo  "1. test                     Run nosetests for firecloud"
	@echo  "2. install                  Install locally with pip"
	@echo  "3. uninstall                Uninstall with pip"
	@echo  "4. publish                  Submit to PyPI"
	@echo


install:
	pip install --upgrade .

reinstall:
	$(MAKE) uninstall
	$(MAKE) install

uninstall:
	pip uninstall -y gdctools

publish:
	python setup.py sdist upload && \
	rm -rf build dist *.egg-info

clean:
	rm -rf build dist *.egg-info

.PHONY: help test install release publish clean
