{{- define "queuelens.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "queuelens.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "queuelens.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "queuelens.selectorLabels" -}}
app.kubernetes.io/name: {{ include "queuelens.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "queuelens.labels" -}}
helm.sh/chart: {{ include "queuelens.chart" . }}
{{ include "queuelens.selectorLabels" . }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "queuelens.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "queuelens.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* The Secret every secret setting comes from: the user's, or the one the chart creates. */}}
{{- define "queuelens.secretName" -}}
{{- default (include "queuelens.fullname" .) .Values.existingSecret }}
{{- end }}

{{/* An env var value: strings as they are, anything else as JSON, so 1048576 does not
turn into 1.048576e+06 (Helm reads numbers as floats) and a map becomes a JSON setting. */}}
{{- define "queuelens.envValue" -}}
{{- if kindIs "string" . }}{{ . }}{{ else }}{{ toJson . }}{{ end }}
{{- end }}

{{/* Settings the app would otherwise default to well-known credentials for. */}}
{{- define "queuelens.requiredSecretKeys" -}}
QUEUELENS_ADMIN_PASSWORD QUEUELENS_RABBITMQ_URL QUEUELENS_RABBITMQ_MANAGEMENT_PASSWORD
{{- end }}
