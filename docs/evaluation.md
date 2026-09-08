# Model evaluation

The initial model will classify Umamusume screens in recorded gameplay. Evaluation will compare recognition quality and CPU cost across template matching, frozen pretrained features, and fine-tuning. No model results are available yet.

## Dataset

Starting categories are training, event dialog, race result, home/menu, and other. A recording audit will establish label definitions and examples of ambiguous transitions before the taxonomy is frozen.

Each source manifest will record the recording hash, parent session, dimensions, duration, language/layout, timestamps, labels, annotator, and dataset version. Publication permission will be recorded separately from application access.

An initial annotation pilot will use 200–500 diverse frames to identify label problems. Collection will then expand across independent sessions and rare screen types. Near-duplicate frames will not substitute for recording diversity.

Stable intervals may be labeled together, with separate inspection of boundary frames. Raw recordings will remain outside Git; the repository will contain manifests and selected publishable fixtures.

## Splits

Train, validation, and test sets will be grouped by source recording/session. Clips cut from the same run, duplicates, and augmentations will stay in the same split. An initial 60/20/20 group split is provisional and will be adjusted for class coverage before evaluation.

Small datasets will use grouped cross-validation for exploratory measurements, with new sessions reserved for final testing. Model selection, thresholds, and preprocessing will use training/validation only.

Randomly splitting neighboring frames would leak nearly identical scenes across sets. Confidence intervals will therefore resample independent recordings where sufficient groups are available, and reports will disclose the group count.

## Comparisons

| Condition | Method |
|---|---|
| Template baseline | Fixed-region image matching with validation-selected thresholds |
| Frozen features | Pretrained small vision backbone with a trained linear classifier |
| Fine-tuned model | The same backbone and head adapted to the labeled recordings |

Candidate backbones include small ResNet and MobileNet variants. Comparisons will share labels, input regions, and held-out data. Architecture, initialization weights, preprocessing, and dependency versions will be recorded.

Training will begin with the new classifier head and then evaluate unfreezing the backbone with a smaller learning rate. Checkpoint selection will use validation performance. Finalists will be repeated across training seeds when resources permit.

Augmentations will reflect supported recordings: moderate brightness, scaling, and compression changes. Transforms that remove or reverse label-defining UI will be excluded unless representative of actual supported inputs. The implementation can follow the standard [PyTorch transfer-learning pattern](https://docs.pytorch.org/tutorials/beginner/transfer_learning_tutorial.html).

## Confidence and temporal consistency

The classifier will include an other category and an abstention path. Neither alone establishes reliable detection of every unseen screen.

Thresholds will be selected on validation data. Reports will include automatic coverage alongside accuracy of accepted predictions. Model scores will not be presented as calibrated correctness probabilities without calibration evidence.

Temporal smoothing will be evaluated separately from raw frame classification. The pipeline will preserve supporting frame timestamps and report missed short events or delayed boundaries.

## Metrics

| Component | Measurements |
|---|---|
| Screen recognition | Macro-F1, per-class precision/recall, confusion matrix, coverage, accepted-read accuracy |
| Event assembly | Event precision/recall, false events per minute, missed events, boundary timing error |
| Field extraction | Exact-match accuracy and coverage for each selected field |
| Runtime | CPU inference latency, end-to-end throughput, peak memory, model size |

Event evaluation will use hand-labeled whole-clip timelines and one-to-one matching by type and a predefined temporal criterion. Matching rules will be fixed before final testing.

OCR quality will be measured independently of screen classification. Visible stat and turn extraction will begin with a few supported fields; unreadable values will remain unknown.

Results will include representative errors, class counts by recording/split, hardware details, sampling settings, and variability across training runs where available. The deployed method will be selected from measured quality and resource use; fine-tuning is not assumed to outperform the baseline.

## Corrections and dataset versions

User corrections will be stored separately from predictions and reviewed before becoming training labels. Contributing data to model development will require opt-in. Existing test recordings will remain excluded from subsequent training sets used for the same reported comparison.

Every dataset update will record provenance and split membership. New analyses will preserve their model and dataset-version references so changes can be traced.

## Model release

Each model artifact will include a manifest with data/split hashes, architecture, initialization weights, training configuration, seed, preprocessing, selected checkpoint, evaluation output, and checksum.

The worker will load weights once per process and stream bounded batches of sampled frames. Cloud deployment will use measured CPU and memory requirements. Runtime conversions such as quantization or ONNX export will require a quality comparison before adoption.

Release checks will cover the report contract, held-out evaluation, and a full pipeline fixture. Previous artifacts will remain available for rollback. Reproduction will preserve configuration and evidence without promising byte-identical training across different hardware or library versions.
